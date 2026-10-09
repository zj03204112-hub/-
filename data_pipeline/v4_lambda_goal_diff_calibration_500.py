#!/usr/bin/env python3
"""Leakage-safe lambda and absolute goal-difference bucket calibration.
Frozen 500 sample: first 300 train, next 100 diagnostic calibration, last 100 blind holdout.
Only the first 300 labels fit any correction. Experimental only; never changes production.
"""
import csv, json, math, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_pipeline"))
import compare_models as cm

DB = ROOT / "football_model_database.sqlite"
MAP = ROOT / "model_validation/500_match_to_db_match_id_mapping.csv"
OUT_JSON = ROOT / "model_validation/v4_lambda_goal_diff_calibration_500_summary.json"
OUT_CSV = ROOT / "model_validation/v4_lambda_goal_diff_calibration_500_predictions.csv"
CLASSES = ["0", "1", "2", "3+"]
EPS = 1e-10


def dt(s):
    return datetime.fromisoformat(str(s)[:19])


def actual_result(r):
    return "H" if r[4] > r[5] else "D" if r[4] == r[5] else "A"


def diff_bucket(r):
    d = abs(int(r[4]) - int(r[5]))
    return "0" if d == 0 else "1" if d == 1 else "2" if d == 2 else "3+"


def poisson_calibrate(lambdas, goals):
    """Fit log(lambda_corrected)=intercept+slope*log(lambda) by Poisson NLL on train only."""
    x = np.log(np.clip(np.asarray(lambdas, dtype=float), 0.05, 5.0))
    y = np.asarray(goals, dtype=float)

    def objective(beta):
        z = np.clip(beta[0] + beta[1] * x, -4.0, 3.0)
        mu = np.exp(z)
        return float(np.mean(mu - y * z + np.array([math.lgamma(v + 1.0) for v in y])) + 0.01 * beta[1] ** 2)

    fit = minimize(objective, np.array([0.0, 1.0]), method="L-BFGS-B",
                   bounds=[(-2.0, 2.0), (0.25, 1.75)])
    if not fit.success and not np.isfinite(fit.fun):
        raise RuntimeError("Poisson lambda calibration failed: " + str(fit.message))
    return [float(fit.x[0]), float(fit.x[1])]


def apply_lambda(lam, params):
    return float(np.clip(math.exp(params[0] + params[1] * math.log(max(0.05, float(lam)))), 0.05, 4.5))


def score_matrix(lh, la):
    return cm.matrix(float(lh), float(la), True)


def hda_probs(m):
    p = {
        "H": sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i > j),
        "D": sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i == j),
        "A": sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i < j),
    }
    s = sum(p.values())
    return {k: v / s for k, v in p.items()}


def diff_probs(m):
    out = {k: 0.0 for k in CLASSES}
    for i in range(cm.N):
        for j in range(cm.N):
            d = abs(i - j)
            key = "0" if d == 0 else "1" if d == 1 else "2" if d == 2 else "3+"
            out[key] += m[i][j]
    s = sum(out.values())
    return {k: max(EPS, v / s) for k, v in out.items()}


def multiclass_metrics(y, probs, labels):
    probs = np.asarray(probs, dtype=float)
    probs = np.clip(probs, EPS, 1.0)
    probs = probs / probs.sum(axis=1, keepdims=True)
    pred = [labels[int(i)] for i in probs.argmax(axis=1)]
    onehot = np.zeros_like(probs)
    idx = {c: i for i, c in enumerate(labels)}
    for i, val in enumerate(y):
        onehot[i, idx[val]] = 1.0
    brier = float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))
    return {
        "n": int(len(y)),
        "accuracy": float(np.mean(np.asarray(pred) == np.asarray(y))),
        "brier": brier,
        "log_loss": float(log_loss(y, probs, labels=labels)),
        "actual_counts": {c: int(sum(v == c for v in y)) for c in labels},
        "predicted_counts": {c: int(sum(v == c for v in pred)) for c in labels},
        "confusion_matrix": [[int(sum(a == true and p == predc for a, p in zip(y, pred)))
                              for predc in labels] for true in labels],
    }


def hda_metrics(rows, probs):
    y = [actual_result(r) for r in rows]
    arr = np.array([[p["H"], p["D"], p["A"]] for p in probs], dtype=float)
    return multiclass_metrics(y, arr, ["H", "D", "A"])


def lambda_metrics(rows, lambdas, side):
    goals = np.array([r[4] if side == "home" else r[5] for r in rows], dtype=float)
    pred = np.array([x[0] if side == "home" else x[1] for x in lambdas], dtype=float)
    err = pred - goals
    return {
        "n": int(len(rows)),
        "actual_mean_goals": float(np.mean(goals)),
        "predicted_mean_lambda": float(np.mean(pred)),
        "mean_bias_lambda_minus_goals": float(np.mean(err)),
        "mae_lambda_vs_goals": float(np.mean(np.abs(err))),
        "rmse_lambda_vs_goals": float(np.sqrt(np.mean(err ** 2))),
    }


def grouped_lambda_bias(rows, lambdas, group_values, side):
    out = {}
    for group in sorted(set(group_values), key=str):
        ix = [i for i, g in enumerate(group_values) if g == group]
        sub_rows = [rows[i] for i in ix]
        sub_lam = [lambdas[i] for i in ix]
        out[str(group)] = lambda_metrics(sub_rows, sub_lam, side)
    return out


def probability_rows_for_lambdas(lambdas):
    mats = [score_matrix(lh, la) for lh, la in lambdas]
    return [hda_probs(m) for m in mats], [diff_probs(m) for m in mats]


def main():
    if not DB.exists() or not MAP.exists():
        raise FileNotFoundError("Required frozen database or mapping file is missing.")
    con = sqlite3.connect(str(DB))
    cm.HIST_LIMIT = 30
    with MAP.open(encoding="utf-8-sig", newline="") as f:
        mapping = list(csv.DictReader(f))
    if len(mapping) != 500 or len({x["match_id"] for x in mapping}) != 500:
        raise ValueError("Frozen mapping must contain exactly 500 unique match IDs.")
    if any(x.get("mapping_status") != "unique" for x in mapping):
        raise ValueError("Mapping status gate failed: all 500 mappings must be unique.")
    db = {r[0]: r for r in con.execute("""
        SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
    """)}
    if any(x["match_id"] not in db for x in mapping):
        raise ValueError("At least one frozen match ID is unresolved in the database.")
    league_by_id = {x["match_id"]: (x.get("league") or x.get("competition_code") or "UNKNOWN") for x in mapping}
    rows = sorted([db[x["match_id"]] for x in mapping], key=lambda r: (dt(r[1]), r[0]))
    if len(rows) != 500:
        raise ValueError("Resolved sample size is not 500.")
    train, cal, hold = rows[:300], rows[300:400], rows[400:]
    split_by_id = {r[0]: ("train" if i < 300 else "calibration" if i < 400 else "blind_holdout")
                   for i, r in enumerate(rows)}

    # Generate leakage-safe T-12h V4 lambdas, using only pre-kickoff history.
    raw_lambdas = []
    strength_gaps = []
    for r in rows:
        lh, la = cm.model_lambdas(con, "v4", r)
        raw_lambdas.append((float(lh), float(la)))
        cutoff = (dt(r[1]) - timedelta(hours=12)).isoformat(timespec="seconds")
        sh = cm.strength(con, r[2], cutoff)
        sa = cm.strength(con, r[3], cutoff)
        strength_gaps.append(abs(float(sh) - float(sa)))

    # Fit two independent Poisson calibrators on the first 300 matches only.
    train_raw = raw_lambdas[:300]
    home_params = poisson_calibrate([x[0] for x in train_raw], [r[4] for r in train])
    away_params = poisson_calibrate([x[1] for x in train_raw], [r[5] for r in train])
    corrected_lambdas = [(apply_lambda(lh, home_params), apply_lambda(la, away_params))
                         for lh, la in raw_lambdas]

    raw_hda, raw_diff = probability_rows_for_lambdas(raw_lambdas)
    corrected_hda, corrected_diff = probability_rows_for_lambdas(corrected_lambdas)

    # Train a four-bucket probability calibrator on train only; inputs are log bucket probabilities.
    x_train = np.array([[math.log(max(EPS, p[k])) for k in CLASSES] for p in raw_diff[:300]])
    y_train = [diff_bucket(r) for r in train]
    bucket_model = LogisticRegression(C=0.1, max_iter=3000, solver="lbfgs")
    bucket_model.fit(x_train, y_train)
    bucket_class_order = list(bucket_model.classes_)

    calibrated_diff = []
    for p in raw_diff:
        x = np.array([[math.log(max(EPS, p[k])) for k in CLASSES]])
        q = bucket_model.predict_proba(x)[0]
        row = {k: EPS for k in CLASSES}
        for i, c in enumerate(bucket_class_order):
            row[c] = float(q[i])
        total = sum(row.values())
        calibrated_diff.append({k: row[k] / total for k in CLASSES})

    slices = {
        "train": (0, 300),
        "calibration": (300, 400),
        "blind_holdout": (400, 500),
    }
    summary = {
        "experiment": "V4 lambda and absolute goal-difference bucket calibration",
        "status": "EXPERIMENT_ONLY_NOT_PRODUCTION",
        "sample_gate": {"rows": len(rows), "unique_match_id": len({r[0] for r in rows}),
                        "mapping_unique": all(x.get("mapping_status") == "unique" for x in mapping)},
        "split": {k: {"n": b-a, "start": rows[a][1], "end": rows[b-1][1]} for k, (a,b) in slices.items()},
        "lambda_calibration": {
            "fit_on": "first 300 chronological matches only",
            "home_loglinear_params_intercept_slope": home_params,
            "away_loglinear_params_intercept_slope": away_params,
            "raw_train_home": lambda_metrics(train, raw_lambdas[:300], "home"),
            "corrected_train_home": lambda_metrics(train, corrected_lambdas[:300], "home"),
            "raw_train_away": lambda_metrics(train, raw_lambdas[:300], "away"),
            "corrected_train_away": lambda_metrics(train, corrected_lambdas[:300], "away"),
            "slices": {}
        },
        "absolute_goal_difference_bucket_calibration": {
            "classes": CLASSES,
            "classifier_fit_on": "first 300 chronological matches only",
            "C": 0.1,
            "slices": {}
        },
        "hda_from_calibrated_lambdas": {},
        "stratified_raw_lambda_bias": {},
        "per_match_errors": {
            "blind_holdout": [],
            "actual_draw_missed_by_raw_hda": [],
            "raw_false_draws": [],
            "actual_draw_missed_by_corrected_lambda_hda": [],
            "corrected_lambda_false_draws": []
        },
        "production_gate": "EXPERIMENT_ONLY; do not promote unless blind holdout improves H/D/A and probability scores without materially worsening draw precision/recall or goal-difference calibration."
    }

    for label, (a, b) in slices.items():
        sl_rows = rows[a:b]
        raw_l = raw_lambdas[a:b]
        cor_l = corrected_lambdas[a:b]
        summary["lambda_calibration"]["slices"][label] = {
            "raw_home": lambda_metrics(sl_rows, raw_l, "home"),
            "corrected_home": lambda_metrics(sl_rows, cor_l, "home"),
            "raw_away": lambda_metrics(sl_rows, raw_l, "away"),
            "corrected_away": lambda_metrics(sl_rows, cor_l, "away"),
            "raw_total_lambda_mean": float(np.mean([h+a for h,a in raw_l])),
            "corrected_total_lambda_mean": float(np.mean([h+a for h,a in cor_l])),
            "actual_total_goals_mean": float(np.mean([r[4]+r[5] for r in sl_rows]))
        }
        actual_buckets = [diff_bucket(r) for r in sl_rows]
        raw_probs = np.array([[raw_diff[i][k] for k in CLASSES] for i in range(a,b)])
        cal_probs = np.array([[calibrated_diff[i][k] for k in CLASSES] for i in range(a,b)])
        summary["absolute_goal_difference_bucket_calibration"]["slices"][label] = {
            "raw_probability_metrics": multiclass_metrics(actual_buckets, raw_probs, CLASSES),
            "calibrated_probability_metrics": multiclass_metrics(actual_buckets, cal_probs, CLASSES),
            "actual_bucket_counts": {k: int(actual_buckets.count(k)) for k in CLASSES},
            "raw_mean_predicted_bucket_probability": {k: float(np.mean(raw_probs[:, j])) for j,k in enumerate(CLASSES)},
            "calibrated_mean_predicted_bucket_probability": {k: float(np.mean(cal_probs[:, j])) for j,k in enumerate(CLASSES)}
        }
        summary["hda_from_calibrated_lambdas"][label] = {
            "raw_lambda": hda_metrics(sl_rows, raw_hda[a:b]),
            "corrected_lambda": hda_metrics(sl_rows, corrected_hda[a:b])
        }

    # Error structure across all 500, segmented by league, strength gap, and realized goal-difference bucket.
    league = [league_by_id[r[0]] for r in rows]
    strength_group = ["close(<0.10)" if g < 0.10 else "medium(0.10-0.25)" if g < 0.25 else "large(>=0.25)"
                      for g in strength_gaps]
    goal_groups = [diff_bucket(r) for r in rows]
    for group_name, values in [("league", league), ("absolute_strength_gap", strength_group),
                               ("actual_abs_goal_difference", goal_groups)]:
        summary["stratified_raw_lambda_bias"][group_name] = {
            "home": grouped_lambda_bias(rows, raw_lambdas, values, "home"),
            "away": grouped_lambda_bias(rows, raw_lambdas, values, "away")
        }

    all_predictions = []
    for i, r in enumerate(rows):
        raw_p = raw_hda[i]
        cor_p = corrected_hda[i]
        raw_pred = max(["H","D","A"], key=lambda c: raw_p[c])
        cor_pred = max(["H","D","A"], key=lambda c: cor_p[c])
        entry = {
            "match_id": r[0], "kickoff": r[1], "league": league[i], "home": r[2], "away": r[3],
            "actual_result": actual_result(r), "actual_home_goals": int(r[4]), "actual_away_goals": int(r[5]),
            "actual_abs_goal_diff_bucket": goal_groups[i], "strength_gap_abs": strength_gaps[i],
            "split": split_by_id[r[0]],
            "lambda_home_raw": raw_lambdas[i][0], "lambda_away_raw": raw_lambdas[i][1],
            "lambda_home_corrected": corrected_lambdas[i][0], "lambda_away_corrected": corrected_lambdas[i][1],
            "raw_pred_hda": raw_pred, "corrected_lambda_pred_hda": cor_pred,
            "raw_p_home": raw_p["H"], "raw_p_draw": raw_p["D"], "raw_p_away": raw_p["A"],
            "corrected_p_home": cor_p["H"], "corrected_p_draw": cor_p["D"], "corrected_p_away": cor_p["A"],
            "raw_bucket_pred": max(CLASSES, key=lambda c: raw_diff[i][c]),
            "calibrated_bucket_pred": max(CLASSES, key=lambda c: calibrated_diff[i][c]),
            "raw_bucket_prob_0": raw_diff[i]["0"], "raw_bucket_prob_1": raw_diff[i]["1"],
            "raw_bucket_prob_2": raw_diff[i]["2"], "raw_bucket_prob_3plus": raw_diff[i]["3+"],
            "cal_bucket_prob_0": calibrated_diff[i]["0"], "cal_bucket_prob_1": calibrated_diff[i]["1"],
            "cal_bucket_prob_2": calibrated_diff[i]["2"], "cal_bucket_prob_3plus": calibrated_diff[i]["3+"]
        }
        all_predictions.append(entry)
        if i >= 400:
            summary["per_match_errors"]["blind_holdout"].append(entry)
            if entry["actual_result"] == "D" and raw_pred != "D":
                summary["per_match_errors"]["actual_draw_missed_by_raw_hda"].append(entry)
            if entry["actual_result"] != "D" and raw_pred == "D":
                summary["per_match_errors"]["raw_false_draws"].append(entry)
            if entry["actual_result"] == "D" and cor_pred != "D":
                summary["per_match_errors"]["actual_draw_missed_by_corrected_lambda_hda"].append(entry)
            if entry["actual_result"] != "D" and cor_pred == "D":
                summary["per_match_errors"]["corrected_lambda_false_draws"].append(entry)

    # CSV is the detailed all-500 table; summary JSON holds holdout error subsets.
    fields = list(all_predictions[0].keys())
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_predictions)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k != "per_match_errors"}, ensure_ascii=False, indent=2))
    print("HOLDOUT_ERROR_COUNTS", json.dumps({k: len(v) for k,v in summary["per_match_errors"].items()}, ensure_ascii=False))
    con.close()


if __name__ == "__main__":
    main()
