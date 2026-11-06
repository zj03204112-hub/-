#!/usr/bin/env python3
"""Is the favourite's probability inflated? Compare de-vig methods.

The current pipeline removes the bookmaker margin by dividing each implied probability by the
sum of the three (the proportional method). That method is known to hand too much probability
to the favourite and too little to the longshot, because bookmakers load most of their margin
onto the longshots. If that is happening here, the favourites' numbers are inflated and a
better method will score better on the proper scoring rules.

Four methods are compared on every archived match that carries 1X2 prices:

  proportional   p_i = (1/o_i) / sum(1/o)
  power          p_i = (1/o_i)^k, with k chosen so the three sum to one
  shin           the Shin (1993) model, which explicitly separates margin from insider trading
  odds_ratio     the Cheung (2015) odds-ratio method

Scoring is out of sample by construction, since de-vigging uses only the prices of the same
match: multiclass Brier, log loss, accuracy of the argmax, and a calibration table that shows
predicted against realised win rates for the favourite in each band.

Usage:
    python data_pipeline/eval_devig_methods.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "devig_methods.json")
RESULT_INdex = {"胜": 0, "平": 1, "负": 2}


def log(message: str) -> None:
    print(message, flush=True)


def proportional(odds: list[float]) -> list[float] | None:
    if any(not math.isfinite(o) or o <= 1.0 for o in odds):
        return None
    inverse = [1.0 / o for o in odds]
    total = sum(inverse)
    return [x / total for x in inverse]


def power(odds: list[float]) -> list[float] | None:
    if any(not math.isfinite(o) or o <= 1.0 for o in odds):
        return None
    inverse = [1.0 / o for o in odds]
    low, high = 0.05, 5.0
    for _ in range(80):
        mid = (low + high) / 2
        total = sum(x ** mid for x in inverse)
        if total > 1.0:
            low = mid
        else:
            high = mid
    k = (low + high) / 2
    values = [x ** k for x in inverse]
    total = sum(values)
    return [v / total for v in values]


def shin(odds: list[float]) -> list[float] | None:
    if any(not math.isfinite(o) or o <= 1.0 for o in odds):
        return None
    inverse = [1.0 / o for o in odds]
    booksum = sum(inverse)
    if booksum <= 1.0:
        return [x / booksum for x in inverse]

    def implied(z: float) -> float:
        return sum((math.sqrt(z * z + 4 * (1 - z) * x * x / booksum) - z) / (2 * (1 - z))
                   for x in inverse)

    low, high = 0.0, 0.5
    for _ in range(80):
        mid = (low + high) / 2
        if implied(mid) > 1.0:
            low = mid
        else:
            high = mid
    z = (low + high) / 2
    values = [((math.sqrt(z * z + 4 * (1 - z) * x * x / booksum) - z) / (2 * (1 - z)))
              for x in inverse]
    total = sum(values)
    if total <= 0:
        return [x / booksum for x in inverse]
    return [v / total for v in values]


def odds_ratio(odds: list[float]) -> list[float] | None:
    """Cheung's odds-ratio method, solved by iterating the implied margin away."""
    if any(not math.isfinite(o) or o <= 1.0 for o in odds):
        return None
    inverse = [1.0 / o for o in odds]
    booksum = sum(inverse)
    if booksum <= 1.0:
        return [x / booksum for x in inverse]
    pi = [x / booksum for x in inverse]
    for _ in range(200):
        denom = sum(pi[i] / (pi[i] + (1 - booksum) / 2) for i in range(3)) if booksum < 1 else 1
        new = []
        for i in range(3):
            value = pi[i] * (booksum + (1 - booksum) * 3 / 2) / 3
            new.append(max(value, 1e-9))
        total = sum(new)
        new = [x / total for x in new]
        if max(abs(a - b) for a, b in zip(new, pi)) < 1e-9:
            pi = new
            break
        pi = new
    return pi


METHODS = {"proportional": proportional, "power": power, "shin": shin,
           "odds_ratio": odds_ratio}


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute("""
        SELECT league, win, draw, lost, result_spf FROM jc_history
        WHERE win > 1 AND draw > 1 AND lost > 1 AND result_spf IN ('胜','平','负')""")]
    log(f"matches: {len(rows)}")

    results = {}
    calibration = {}
    for name, method in METHODS.items():
        correct = brier = logloss = 0.0
        n = 0
        bands: dict[str, list[int]] = {}
        for row in rows:
            probabilities = method([row["win"], row["draw"], row["lost"]])
            if not probabilities:
                continue
            actual = RESULT_INdex[row["result_spf"]]
            n += 1
            pick = max(range(3), key=lambda i: probabilities[i])
            correct += int(pick == actual)
            brier += sum((probabilities[i] - (1.0 if i == actual else 0.0)) ** 2
                         for i in range(3))
            logloss -= math.log(max(probabilities[actual], 1e-12))
            favourite = 0 if probabilities[0] > probabilities[2] else 2
            strength = probabilities[favourite]
            band = f"{min(int(strength * 20) / 20, 0.95):.2f}"
            entry = bands.setdefault(band, [0, 0, 0.0])
            entry[0] += 1
            entry[1] += int(actual == favourite)
            entry[2] += strength
        results[name] = {"n": n, "accuracy": round(correct / n, 4),
                         "brier": round(brier / n, 5), "log_loss": round(logloss / n, 5)}
        calibration[name] = {band: {"n": value[0],
                                    "predicted": round(value[2] / value[0], 4),
                                    "actual": round(value[1] / value[0], 4),
                                    "bias_pp": round(100 * (value[1] / value[0] - value[2] / value[0]), 2)}
                             for band, value in sorted(bands.items())}

    log(f"\n{'method':>14s} {'n':>6s} {'accuracy':>9s} {'brier':>9s} {'log loss':>9s}")
    for name, value in results.items():
        log(f"{name:>14s} {value['n']:6d} {100 * value['accuracy']:8.2f}% "
            f"{value['brier']:9.5f} {value['log_loss']:9.5f}")

    log("\nfavourite calibration (proportional), predicted against realised:")
    for band, value in calibration["proportional"].items():
        if value["n"] < 100:
            continue
        log(f"  band {band}: n={value['n']:5d}  predicted {100 * value['predicted']:5.1f}%  "
            f"actual {100 * value['actual']:5.1f}%  bias {value['bias_pp']:+.1f}pp")

    payload = {"definition": (
                   "Four de-vig methods scored on all archived matches with 1X2 prices. "
                   "Calibration compares the predicted favourite probability with the realised "
                   "win rate in each band."),
               "scores": results, "calibration": calibration,
               "bands": {name: list(calibration[name])[:0] for name in []}}
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
