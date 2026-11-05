#!/usr/bin/env python3
"""Do team strength and match importance add anything beyond the market?

Two ideas are tested that earlier work did not cover:

  strength   an Elo rating built chronologically from every result in the archive, plus
             rolling points and goal difference over the last five matches
  importance season phase (the closing months), whether the fixture is a cup tie, and the
             stakes implied by both sides' recent form (a top clash or a relegation fight)

Both are evaluated the only way that matters: a logistic model trained on the earlier seasons
and scored on the later ones, against the market's own probability as the baseline. For draws
the metrics are AUC, Brier and precision in the top decile; for the three-way they are accuracy,
Brier and log loss.

Nothing is allowed to peek: Elo, form and table position are updated only after a match.

Usage:
    python data_pipeline/eval_importance_strength.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "importance_strength.json")

CUP_MARKERS = ("杯", "联赛杯", "足总杯", "国王杯", "德国杯", "意大利杯", "法国杯")
TRAIN_YEARS = ("2022", "2023", "2024")
TEST_YEARS = ("2025", "2026")


def log(message: str) -> None:
    print(message, flush=True)


def devig(values: list[float]) -> list[float] | None:
    if any(v is None or not math.isfinite(v) or v <= 1.0 for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def season_of(day: str) -> str:
    year, month = int(day[:4]), int(day[5:7])
    return f"{year}-{year + 1}" if month >= 7 else f"{year - 1}-{year}"


def build_features() -> list[dict]:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute("""
        SELECT match_date, league, home, away, win, draw, lost, result_spf,
               home_score, away_score
        FROM jc_history
        WHERE win > 1 AND draw > 1 AND lost > 1 AND result_spf IN ('胜','平','负')
          AND home_score >= 0 AND away_score >= 0
        ORDER BY match_date, matchnum""")]

    elo: dict[str, float] = defaultdict(lambda: 1500.0)
    form_points: dict[str, list[float]] = defaultdict(list)
    form_goals: dict[str, list[float]] = defaultdict(list)
    table: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    samples = []
    for row in rows:
        probabilities = devig([row["win"], row["draw"], row["lost"]])
        if not probabilities:
            continue
        home, away, day = row["home"], row["away"], row["match_date"]
        league, season = row["league"], season_of(day)

        elo_home, elo_away = elo[home], elo[away]
        home_points = form_points[home][-5:]
        away_points = form_points[away][-5:]
        home_goals = form_goals[home][-5:]
        away_goals = form_goals[away][-5:]

        home_ppg = sum(home_points) / len(home_points) if home_points else 1.4
        away_ppg = sum(away_points) / len(away_points) if away_points else 1.4
        home_gd = sum(home_goals) / len(home_goals) if home_goals else 0.0
        away_gd = sum(away_goals) / len(away_goals) if away_goals else 0.0

        standings = table[(league, season)]
        home_row = standings.get(home, [0, 0])
        away_row = standings.get(away, [0, 0])
        home_mp, home_pts = home_row[0], home_row[1]
        away_mp, away_pts = away_row[0], away_row[1]
        home_pts_per_game = home_pts / home_mp if home_mp >= 3 else None
        away_pts_per_game = away_pts / away_mp if away_mp >= 3 else None

        month = int(day[5:7])
        is_cup = any(marker in league for marker in CUP_MARKERS)
        stakes_close = (abs(home_ppg - away_ppg) < 0.4)   # evenly matched in form

        samples.append({
            "date": day, "year": day[:4], "league": league,
            "draw": row["result_spf"] == "平",
            "actual": {"胜": 0, "平": 1, "负": 2}[row["result_spf"]],
            "p_draw": probabilities[1], "p": probabilities,
            "elo_diff": (elo_home + 60.0 - elo_away) / 400.0,
            "ppg_diff": home_ppg - away_ppg,
            "gd_diff": home_gd - away_gd,
            "important_month": month in (4, 5),
            "is_cup": float(is_cup),
            "top_clash": float(home_ppg >= 1.8 and away_ppg >= 1.8),
            "relegation_fight": float(home_ppg <= 1.0 and away_ppg <= 1.0),
            "stakes_close": float(stakes_close),
            "table_gap": (abs(home_pts_per_game - away_pts_per_game)
                          if home_pts_per_game is not None and away_pts_per_game is not None else None),
        })

        # update state strictly after the fixture
        margin = row["home_score"] - row["away_score"]
        result = 2.0 if margin > 0 else (0.0 if margin < 0 else 1.0)
        expected = 1.0 / (1.0 + 10 ** ((elo_away - (elo_home + 60.0)) / 400.0))
        multiplier = 1.0 if abs(margin) <= 1 else (1.5 if abs(margin) == 2 else (1.75 if abs(margin) == 3 else 2.0))
        change = 20.0 * multiplier * (result - expected)
        elo[home] = elo_home + change
        elo[away] = elo_away - change
        form_points[home].append(3.0 if margin > 0 else (1.0 if margin == 0 else 0.0))
        form_points[away].append(3.0 if margin < 0 else (1.0 if margin == 0 else 0.0))
        form_goals[home].append(float(margin))
        form_goals[away].append(float(-margin))
        standings[home][0] += 1
        standings[away][0] += 1
        if margin > 0:
            standings[home][1] += 3
        elif margin < 0:
            standings[away][1] += 3
        else:
            standings[home][1] += 1
            standings[away][1] += 1
    return samples


FEATURES = ["elo_diff", "ppg_diff", "gd_diff", "important_month", "is_cup",
            "top_clash", "relegation_fight", "stakes_close"]


def matrix(samples: list[dict], extra: list[str]) -> np.ndarray:
    columns = [np.array([s[f] if s[f] is not None else 0.0 for s in samples], dtype=float)
               for f in extra]
    return np.column_stack(columns) if columns else np.zeros((len(samples), 0))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1e-3, steps: int = 800,
                 lr: float = 0.15) -> np.ndarray:
    X = np.column_stack([np.ones(len(X)), X])
    weights = np.zeros(X.shape[1])
    for _ in range(steps):
        z = X @ weights
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        gradient = X.T @ (p - y) / len(X)
        gradient[1:] += l2 * weights[1:]
        weights -= lr * gradient
    return weights


def predict_logistic(weights: np.ndarray, X: np.ndarray) -> np.ndarray:
    X = np.column_stack([np.ones(len(X)), X])
    z = X @ weights
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def auc(y: np.ndarray, scores: np.ndarray) -> float:
    order = np.argsort(scores)
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    positives = y.sum()
    negatives = len(y) - positives
    if positives == 0 or negatives == 0:
        return 0.5
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def main() -> int:
    samples = build_features()
    train = [s for s in samples if s["year"] in TRAIN_YEARS]
    test = [s for s in samples if s["year"] in TEST_YEARS]
    log(f"matches {len(samples)} (train {len(train)}, test {len(test)})")

    y_train = np.array([1.0 if s["draw"] else 0.0 for s in train])
    y_test = np.array([1.0 if s["draw"] else 0.0 for s in test])
    log(f"draw rate: train {100 * y_train.mean():.1f}%, test {100 * y_test.mean():.1f}%")

    base_train = np.array([s["p_draw"] for s in train])
    base_test = np.array([s["p_draw"] for s in test])

    X_all_train = matrix(train, FEATURES)
    X_all_test = matrix(test, FEATURES)
    logit_train = np.log(np.clip(base_train, 1e-6, 1 - 1e-6) / (1 - np.clip(base_train, 1e-6, 1 - 1e-6)))

    results = {}

    def report(name: str, scores: np.ndarray, probabilities: np.ndarray) -> None:
        top_n = max(1, int(len(scores) * 0.10))
        order = np.argsort(-scores)[:top_n]
        precision = float(y_test[order].mean())
        results[name] = {
            "auc": round(auc(y_test, scores), 4),
            "brier": round(brier(y_test, probabilities), 5),
            "top_decile_precision": round(precision, 4),
            "top_decile_lift_pp": round(100 * (precision - float(y_test.mean())), 2)}

    report("market_only", base_test, base_test)

    for name, extra in (("features_only", FEATURES),
                        ("market_plus_features", FEATURES)):
        X_tr = np.column_stack([logit_train, matrix(train, extra)]) if name.endswith("plus_features") \
            else matrix(train, extra)
        X_te = np.column_stack([np.log(np.clip(base_test, 1e-6, 1 - 1e-6) /
                                       (1 - np.clip(base_test, 1e-6, 1 - 1e-6))),
                                matrix(test, extra)]) if name.endswith("plus_features") \
            else matrix(test, extra)
        weights = fit_logistic(X_tr, y_train)
        probabilities = predict_logistic(weights, X_te)
        logit_scores = np.log(np.clip(probabilities, 1e-6, 1 - 1e-6) /
                              (1 - np.clip(probabilities, 1e-6, 1 - 1e-6)))
        report(name, logit_scores, probabilities)

    log("\ndraw prediction, out of sample (2025-2026):")
    for name, value in results.items():
        log(f"  {name:22s} AUC {value['auc']:.4f}  Brier {value['brier']:.5f}  "
            f"top-10% precision {100 * value['top_decile_precision']:.1f}% "
            f"({value['top_decile_lift_pp']:+.1f}pp)")

    # three-way accuracy: does the model beat the market argmax?
    def three_way_accuracy(probabilities: list[list[float]]) -> float:
        correct = 0
        for sample, triple in zip(test, probabilities):
            pick = max(range(3), key=lambda i: triple[i])
            correct += int(pick == sample["actual"])
        return correct / len(test)

    market_accuracy = three_way_accuracy([s["p"] for s in test])

    # a simple correction: shrink the market towards a model that only sees draw signal
    weights = fit_logistic(np.column_stack([logit_train, X_all_train]), y_train)
    draw_model = predict_logistic(weights, np.column_stack([
        np.log(np.clip(base_test, 1e-6, 1 - 1e-6) / (1 - np.clip(base_test, 1e-6, 1 - 1e-6))),
        X_all_test]))
    blended = []
    for sample, p_draw in zip(test, draw_model):
        home, draw, away = sample["p"]
        rest = home + away
        scale = (1.0 - p_draw) / rest if rest > 0 else 0.0
        blended.append([home * scale, p_draw, away * scale])
    blended_accuracy = three_way_accuracy(blended)

    log(f"\nthree-way accuracy: market {100 * market_accuracy:.2f}%  "
        f"market+draw-model {100 * blended_accuracy:.2f}%")

    payload = {"definition": (
                   "Team strength (Elo, rolling form, table gap) and match importance (season "
                   "phase, cup tie, stakes) tested chronologically: trained on 2022-2024, scored "
                   "on 2025-2026, with the market's own probability as the baseline."),
               "matches": {"train": len(train), "test": len(test)},
               "draw_rate": {"train": round(float(y_train.mean()), 4),
                             "test": round(float(y_test.mean()), 4)},
               "draw_prediction": results,
               "three_way_accuracy": {"market": round(market_accuracy, 4),
                                      "market_plus_draw_model": round(blended_accuracy, 4)},
               "caveat": ("A gain has to survive out of sample to matter; the market baseline "
                          "is already calibrated.")}
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
