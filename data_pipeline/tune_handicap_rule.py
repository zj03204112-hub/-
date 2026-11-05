#!/usr/bin/env python3
"""Choose a handicap decision rule properly: tune on early seasons, validate later.

Three rules are compared on the archived jingcai handicap market:

  argmax      pick the most likely of 让胜 / 让平 / 让负 (the current behaviour)
  draw-tolerant  pick 让平 when its probability is at least X times the top probability,
                 otherwise argmax; X is tuned on the training segment
  ev-max      pick the outcome with the highest expected value, p * price - 1, using the
              archived prices

Training segment 2022-2023, validation 2025-2026: the threshold is chosen on the former and
then applied once to the latter, so the reported numbers are out of sample.

Usage:
    python data_pipeline/tune_handicap_rule.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "handicap_rule_tuning.json")
TRAIN = ("2022", "2023")
TEST = ("2025", "2026")
LABELS = {"胜": 0, "平": 1, "负": 2}
NAMES = ("让胜", "让平", "让负")


def log(message: str) -> None:
    print(message, flush=True)


def load(connection) -> list[dict]:
    rows = []
    for row in connection.execute("""
            SELECT match_date, matchnum, home, away, hhad_h, hhad_d, hhad_a, result_hhad,
                   handicap_line, home_score, away_score
            FROM jc_history
            WHERE hhad_h IS NOT NULL AND result_hhad IN ('胜','平','负')
              AND home_score >= 0 AND away_score >= 0"""):
        prices = [row[4], row[5], row[6]]
        if any(p is None or p <= 1 for p in prices):
            continue
        inverse = [1.0 / p for p in prices]
        total = sum(inverse)
        rows.append({"date": row[0], "num": row[1], "home": row[2], "away": row[3],
                     "prices": prices, "probs": [x / total for x in inverse],
                     "actual": LABELS[row[7]], "line": row[8],
                     "score": (row[9], row[10])})
    return rows


def evaluate(rows: list[dict], picker) -> dict:
    if not rows:
        return {"n": 0}
    correct = 0
    stake = 0.0
    for row in rows:
        pick = picker(row)
        if pick is None:
            continue
        correct += int(pick == row["actual"])
        stake += (row["prices"][pick] - 1.0) if pick == row["actual"] else -1.0
    n = len(rows)
    return {"n": n, "accuracy": round(correct / n, 4), "roi": round(stake / n, 4)}


def argmax(row) -> int:
    return max(range(3), key=lambda i: row["probs"][i])


def draw_tolerant(threshold: float):
    def pick(row) -> int:
        probabilities = row["probs"]
        top = max(range(3), key=lambda i: probabilities[i])
        if top != 1 and probabilities[1] >= threshold * probabilities[top]:
            return 1
        return top
    return pick


def ev_max(row) -> int | None:
    values = [row["probs"][i] * row["prices"][i] - 1.0 for i in range(3)]
    return max(range(3), key=lambda i: values[i])


def main() -> int:
    connection = sqlite3.connect(DB)
    rows = load(connection)
    train = [r for r in rows if r["date"][:4] in TRAIN]
    test = [r for r in rows if r["date"][:4] in TEST]
    log(f"season rows: {len(rows)} (train {len(train)}, test {len(test)})")

    base_train = evaluate(train, argmax)
    base_test = evaluate(test, argmax)
    log(f"  argmax train acc={base_train['accuracy']:.3f} roi={base_train['roi']:+.3f} | "
        f"test acc={base_test['accuracy']:.3f} roi={base_test['roi']:+.3f}")

    # tune the draw tolerance on the training segment, by accuracy
    curve = []
    best = (None, -1.0)
    for step in range(20, 100):
        threshold = step / 100
        result = evaluate(train, draw_tolerant(threshold))
        curve.append({"threshold": threshold, "train_accuracy": result["accuracy"],
                      "train_roi": result["roi"],
                      "pick_share_draw": round(
                          sum(1 for r in train if draw_tolerant(threshold)(r) == 1) / len(train), 4)})
        if result["accuracy"] > best[1]:
            best = (threshold, result["accuracy"])
    threshold = best[0]
    log(f"  tuned draw tolerance: {threshold:.2f} (train accuracy {best[1]:.4f})")

    test_draw = evaluate(test, draw_tolerant(threshold))
    test_ev = evaluate(test, ev_max)
    log(f"  draw-tolerant test acc={test_draw['accuracy']:.3f} roi={test_draw['roi']:+.3f}")
    log(f"  ev-max        test acc={test_ev['accuracy']:.3f} roi={test_ev['roi']:+.3f}")

    # how often does the draw-tolerance rule actually fire on the test set?
    fired = sum(1 for r in test if draw_tolerant(threshold)(r) == 1)
    draws = sum(1 for r in test if r["actual"] == 1)
    payload = {
        "definition": (
            "Handicap decision rules tuned on 2022-2023 and evaluated once on 2025-2026. "
            "The draw-tolerant rule takes 让平 when its probability reaches X times the top "
            "probability; X is chosen on the training segment by accuracy. ROI is a flat "
            "stake at the archived prices."),
        "counts": {"total": len(rows), "train": len(train), "test": len(test)},
        "argmax": {"train": base_train, "test": base_test},
        "draw_tolerant": {"threshold": threshold, "train": evaluate(train, draw_tolerant(threshold)),
                          "test": test_draw,
                          "test_draw_picks": fired, "test_actual_draws": draws,
                          "test_draw_pick_share": round(fired / len(test), 4)},
        "ev_max": {"test": test_ev},
        "tuning_curve": curve,
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
