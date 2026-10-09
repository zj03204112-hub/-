#!/usr/bin/env python3
"""Experiment-only direct H/D/A classifier using frozen pre-match V4 features.

Chronological split: 300 train, 100 calibration, 100 untouched holdout.
Does not modify production predictions or the frozen source data.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, log_loss

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "model_validation/500_match_V4_frozen_predictions.csv"
OUT_JSON = ROOT / "model_validation/V5_1_DIRECT_HDA_CLASSIFIER_500_SUMMARY.json"
OUT_CSV = ROOT / "model_validation/V5_1_DIRECT_HDA_CLASSIFIER_500_PREDICTIONS.csv"
CLASSES = ["H", "D", "A"]
FEATURES = [
    "p_home", "p_draw", "p_away",
    "strength_home", "strength_away", "strength_gap", "strength_gap_signed",
    "pred_goal_diff", "pred_score_1_prob", "pred_score_2_prob",
    "goal_diff_abs_error",  # removed below: this is post-match and must never be used
]
# Explicit allow-list: only pre-match features. Post-match fields are excluded.
FEATURES = [f for f in FEATURES if f != "goal_diff_abs_error"]
C_GRID = [0.01, 0.1, 1.0, 10.0]


def brier(y_true, probs):
    onehot = np.zeros((len(y_true), len(CLASSES)))
    for i, label in enumerate(y_true):
        onehot[i, CLASSES.index(label)] = 1.0
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def draw_stats(y_true, y_pred):
    tp = sum((a == "D" and p == "D") for a, p in zip(y_true, y_pred))
    fp = sum((a != "D" and p == "D") for a, p in zip(y_true, y_pred))
    fn = sum((a == "D" and p != "D") for a, p in zip(y_true, y_pred))
    return {"tp": int(tp), "fp": int(fp), "fn": int(fn),
            "recall": float(tp / (tp + fn)) if tp + fn else None}


def evaluate(y, probs):
    pred = [CLASSES[i] for i in probs.argmax(axis=1)]
    return {
        "n": int(len(y)),
        "accuracy": float(accuracy_score(y, pred)),
        "draw": draw_stats(y, pred),
        "brier": brier(list(y), probs),
        "log_loss": float(log_loss(y, probs, labels=CLASSES)),
        "confusion_matrix_labels_H_D_A": confusion_matrix(y, pred, labels=CLASSES).tolist(),
        "predicted_class_counts": {k: int(pred.count(k)) for k in CLASSES},
        "actual_class_counts": {k: int(list(y).count(k)) for k in CLASSES},
    }


def make_model(c):
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("classifier", LogisticRegression(C=c, max_iter=3000, solver="lbfgs")),
    ])


def class_probs(model, X):
    raw = model.predict_proba(X)
    model_classes = list(model.named_steps["classifier"].classes_)
    return np.column_stack([raw[:, model_classes.index(k)] if k in model_classes else np.zeros(len(X)) for k in CLASSES])


def main():
    if not SRC.exists():
        raise FileNotFoundError(f"Frozen sample missing: {SRC}")
    df = pd.read_csv(SRC)
    required = {"kickoff", "actual_result", *FEATURES}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    df["kickoff_sort"] = pd.to_datetime(df["kickoff"], format="mixed", errors="coerce")
    df = df.dropna(subset=["kickoff_sort", "actual_result"]).sort_values(["kickoff_sort", "match_id"], kind="stable").reset_index(drop=True)
    df = df[df["actual_result"].isin(CLASSES)].reset_index(drop=True)
    if len(df) != 500:
        raise ValueError(f"Expected exactly 500 eligible frozen rows, got {len(df)}")
    if df["match_id"].nunique() != 500:
        raise ValueError("Frozen match_id uniqueness gate failed")

    X = df[FEATURES].apply(pd.to_numeric, errors="coerce")
    y = df["actual_result"].astype(str).to_numpy()
    # Match order is chronological, never shuffled.
    tr = np.arange(0, 300)
    cal = np.arange(300, 400)
    ho = np.arange(400, 500)

    baseline_probs_all = df[["p_home", "p_draw", "p_away"]].apply(pd.to_numeric, errors="coerce").to_numpy()
    baseline_probs_all = np.clip(baseline_probs_all, 1e-9, None)
    baseline_probs_all = baseline_probs_all / baseline_probs_all.sum(axis=1, keepdims=True)
    baseline_train = evaluate(y[tr], baseline_probs_all[tr])
    baseline_cal = evaluate(y[cal], baseline_probs_all[cal])
    baseline_holdout = evaluate(y[ho], baseline_probs_all[ho])

    candidates = []
    for c in C_GRID:
        model = make_model(c)
        model.fit(X.iloc[tr], y[tr])
        probs = class_probs(model, X.iloc[cal])
        metrics = evaluate(y[cal], probs)
        candidates.append({"C": c, "calibration": metrics})
    # Choose by calibration LogLoss; use accuracy, then simpler regularization as tie-breaks.
    chosen = sorted(candidates, key=lambda x: (x["calibration"]["log_loss"], -x["calibration"]["accuracy"], x["C"]))[0]
    final_model = make_model(chosen["C"])
    fit_idx = np.concatenate([tr, cal])
    final_model.fit(X.iloc[fit_idx], y[fit_idx])
    cal_probs = class_probs(final_model, X.iloc[cal])
    holdout_probs = class_probs(final_model, X.iloc[ho])

    holdout_df = df.iloc[ho][["sample_row", "date", "league", "home", "away", "actual_result", "match_id", "kickoff"]].copy()
    holdout_df["baseline_pred"] = [CLASSES[i] for i in baseline_probs_all[ho].argmax(axis=1)]
    holdout_df["classifier_pred"] = [CLASSES[i] for i in holdout_probs.argmax(axis=1)]
    for i, label in enumerate(CLASSES):
        holdout_df[f"baseline_p_{label}"] = baseline_probs_all[ho, i]
        holdout_df[f"classifier_p_{label}"] = holdout_probs[i]
    holdout_df.to_csv(OUT_CSV, index=False)

    summary = {
        "experiment": "V5.1_DIRECT_HDA_CLASSIFIER",
        "status": "EXPERIMENT_ONLY_NOT_PRODUCTION",
        "source": SRC.name,
        "features": FEATURES,
        "excluded_post_match_features": ["home_score", "away_score", "actual_goal_diff", "goal_diff_abs_error", "actual_result"],
        "split": {"train": 300, "calibration": 100, "final_holdout": 100, "chronological": True, "shuffle": False},
        "rows": len(df),
        "unique_match_id": int(df["match_id"].nunique()),
        "periods": {
            "train": [str(df.iloc[tr[0]]["kickoff"]), str(df.iloc[tr[-1]]["kickoff"])],
            "calibration": [str(df.iloc[cal[0]]["kickoff"]), str(df.iloc[cal[-1]]["kickoff"])],
            "final_holdout": [str(df.iloc[ho[0]]["kickoff"]), str(df.iloc[ho[-1]]["kickoff"])],
        },
        "candidate_grid": candidates,
        "selected_C": chosen["C"],
        "baseline": {"train": baseline_train, "calibration": baseline_cal, "final_holdout": baseline_holdout},
        "classifier": {"calibration_refit_model_metrics": evaluate(y[cal], cal_probs), "final_holdout": evaluate(y[ho], holdout_probs)},
        "holdout_delta_classifier_minus_baseline": {
            "accuracy": evaluate(y[ho], holdout_probs)["accuracy"] - baseline_holdout["accuracy"],
            "brier": evaluate(y[ho], holdout_probs)["brier"] - baseline_holdout["brier"],
            "log_loss": evaluate(y[ho], holdout_probs)["log_loss"] - baseline_holdout["log_loss"],
            "draw_recall": (evaluate(y[ho], holdout_probs)["draw"]["recall"] or 0) - (baseline_holdout["draw"]["recall"] or 0),
        },
        "acceptance": {
            "accuracy_improved": evaluate(y[ho], holdout_probs)["accuracy"] > baseline_holdout["accuracy"],
            "brier_not_worse": evaluate(y[ho], holdout_probs)["brier"] <= baseline_holdout["brier"],
            "log_loss_not_worse": evaluate(y[ho], holdout_probs)["log_loss"] <= baseline_holdout["log_loss"],
            "draw_recall_not_worse": (evaluate(y[ho], holdout_probs)["draw"]["recall"] or 0) >= (baseline_holdout["draw"]["recall"] or 0),
            "production_promotion": False,
        },
        "caveat": "This classifier learns from the frozen prediction table's pre-match probability/strength features. It is not a full-feature retrain and does not establish that new raw team/player data improve performance."
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
