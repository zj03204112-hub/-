#!/usr/bin/env python3
"""Calibrate the favourite's probability so the numbers match reality.

The de-vigged market probability is close but not exact: measured over 12721 matches the
realised win rate of the favourite sits a few points above the predicted value in almost every
band. This fits a two-parameter correction on the logit scale,

    logit(p_corrected) = a * logit(p_market) + b

on the earlier seasons and applies it, untouched, to the later ones. The other two outcomes are
rescaled so the three still sum to one, which keeps the correction coherent as a probability
distribution.

Reported: Brier, log loss and accuracy before and after, plus the calibration table on the
held-out seasons so the improvement can be seen rather than asserted.

Usage:
    python data_pipeline/calibrate_favourite.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "favourite_calibration.json")
RESULT_INDEX = {"胜": 0, "平": 1, "负": 2}
TRAIN_YEARS = ("2022", "2023", "2024")


def log(message: str) -> None:
    print(message, flush=True)


def power_devig(odds: list[float]) -> list[float] | None:
    if any(not math.isfinite(o) or o <= 1.0 for o in odds):
        return None
    inverse = [1.0 / o for o in odds]
    low, high = 0.05, 5.0
    for _ in range(80):
        mid = (low + high) / 2
        if sum(x ** mid for x in inverse) > 1.0:
            low = mid
        else:
            high = mid
    k = (low + high) / 2
    values = [x ** k for x in inverse]
    total = sum(values)
    return [v / total for v in values]


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit(X: np.ndarray, y: np.ndarray, steps: int = 2000, lr: float = 0.1) -> np.ndarray:
    X = np.column_stack([np.ones(len(X)), X])
    weights = np.array([0.0, 1.0])         # start from the identity correction
    for _ in range(steps):
        p = sigmoid(X @ weights)
        gradient = X.T @ (p - y) / len(X)
        weights -= lr * gradient
    return weights


def apply_correction(probabilities: list[float], weights: np.ndarray) -> list[float]:
    favourite = 0 if probabilities[0] > probabilities[2] else 2
    p_favourite = probabilities[favourite]
    corrected = float(sigmoid(weights[0] + weights[1] * logit(p_favourite)))
    corrected = min(max(corrected, 0.02), 0.985)
    rest = 1.0 - p_favourite
    scale = (1.0 - corrected) / rest if rest > 1e-9 else 0.0
    result = [p * scale for p in probabilities]
    result[favourite] = corrected
    total = sum(result)
    return [x / total for x in result]


def score(rows: list[dict], corrector=None) -> dict:
    brier = logloss = 0.0
    correct = 0
    bands: dict[str, list[float]] = {}
    for row in rows:
        probabilities = row["p"] if corrector is None else corrector(row["p"])
        actual = row["actual"]
        pick = max(range(3), key=lambda i: probabilities[i])
        correct += int(pick == actual)
        brier += sum((probabilities[i] - (1.0 if i == actual else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(probabilities[actual], 1e-12))
        favourite = 0 if probabilities[0] > probabilities[2] else 2
        strength = probabilities[favourite]
        band = f"{min(int(strength * 20) / 20, 0.95):.2f}"
        entry = bands.setdefault(band, [0, 0, 0.0])
        entry[0] += 1
        entry[1] += int(actual == favourite)
        entry[2] += strength
    n = len(rows)
    return {"n": n, "accuracy": round(correct / n, 4), "brier": round(brier / n, 5),
            "log_loss": round(logloss / n, 5),
            "calibration": {band: {"n": int(v[0]), "predicted": round(v[2] / v[0], 4),
                                   "actual": round(v[1] / v[0], 4),
                                   "bias_pp": round(100 * (v[1] / v[0] - v[2] / v[0]), 2)}
                            for band, v in sorted(bands.items()) if v[0] >= 100}}


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = []
    for row in connection.execute("""
            SELECT match_date, win, draw, lost, result_spf FROM jc_history
            WHERE win > 1 AND draw > 1 AND lost > 1 AND result_spf IN ('胜','平','负')"""):
        probabilities = power_devig([row["win"], row["draw"], row["lost"]])
        if not probabilities:
            continue
        favourite = 0 if probabilities[0] > probabilities[2] else 2
        rows.append({"date": row["match_date"], "p": probabilities,
                     "actual": RESULT_INDEX[row["result_spf"]],
                     "favourite_won": 1.0 if RESULT_INDEX[row["result_spf"]] == favourite else 0.0,
                     "favourite": favourite})
    train = [r for r in rows if r["date"][:4] in TRAIN_YEARS]
    test = [r for r in rows if r["date"][:4] not in TRAIN_YEARS]
    log(f"matches {len(rows)} (train {len(train)}, test {len(test)})")

    X = np.array([[logit(r["p"][r["favourite"]])] for r in train])
    y = np.array([r["favourite_won"] for r in train])
    weights = fit(X, y)
    log(f"correction: logit(p') = {weights[0]:+.4f} + {weights[1]:.4f} * logit(p)")

    corrector = lambda probabilities: apply_correction(probabilities, weights)
    before = score(test)
    after = score(test, corrector)

    log(f"\nheld-out seasons ({len(test)} matches):")
    log(f"{'':>10s} {'accuracy':>9s} {'brier':>9s} {'log loss':>9s}")
    for label, value in (("before", before), ("after", after)):
        log(f"{label:>10s} {100 * value['accuracy']:8.2f}% {value['brier']:9.5f} "
            f"{value['log_loss']:9.5f}")

    log("\ncalibration before / after on the held-out seasons:")
    log(f"{'band':>6s} {'n':>5s} {'pred':>7s} {'actual':>7s} {'bias':>7s} | "
        f"{'pred':>7s} {'bias':>7s}")
    for band in sorted(set(before["calibration"]) & set(after["calibration"])):
        b, a = before["calibration"][band], after["calibration"][band]
        log(f"{band:>6s} {b['n']:5d} {100 * b['predicted']:6.1f}% {100 * b['actual']:6.1f}% "
            f"{b['bias_pp']:+6.1f} | {100 * a['predicted']:6.1f}% {a['bias_pp']:+6.1f}")

    payload = {"definition": (
                   "A two-parameter logit correction fitted on 2022-2024 and applied unchanged "
                   "to 2025-2026, so the scores on the held-out seasons are honest. The other "
                   "two outcomes are rescaled to keep the triple summing to one."),
               "correction": {"intercept": round(float(weights[0]), 4),
                              "slope": round(float(weights[1]), 4)},
               "train_matches": len(train), "test_matches": len(test),
               "before": {k: v for k, v in before.items() if k != "calibration"},
               "after": {k: v for k, v in after.items() if k != "calibration"},
               "calibration_before": before["calibration"],
               "calibration_after": after["calibration"]}
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
