#!/usr/bin/env python3
"""Read-only audits for recovered Jincai predictions and the frozen 500-match probability file.

This script does not modify V5.1, the frozen sample, or original prediction records.
"""
import csv
import json
import math
import re
from pathlib import Path

PREDICTIONS = Path("predictions/2026-10-11/recovered_original_predictions_004-028.md")
FROZEN = Path("model_validation/500_match_V4_frozen_predictions.csv")
OUT = Path("reviews/2026-10-11/audit_metrics_reproducible.json")


def parse_score(value):
    m = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", value or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def handicap_label(home, away, line):
    adjusted_home = home + line
    return "让胜" if adjusted_home > away else "让平" if adjusted_home == away else "让负"


def read_prediction_rows():
    rows = []
    for line in PREDICTIONS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "编号" in line:
            continue
        cols = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cols) < 8 or not cols[0].isdigit():
            continue
        first = parse_score(cols[3])
        try:
            line_value = int(cols[6])
        except ValueError:
            continue
        stored = cols[7]
        if first is None:
            continue
        computed = handicap_label(first[0], first[1], line_value)
        rows.append({
            "id": int(cols[0]), "match": cols[1], "score1": cols[3],
            "official_handicap_as_recovered": line_value,
            "stored_handicap_prediction": stored,
            "mechanical_handicap_from_score1": computed,
            "consistent": stored == computed,
        })
    return rows


def read_frozen():
    with FROZEN.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def score_metrics(rows, multiplier=1.0):
    correct = draw_tp = actual_draws = predicted_draws = 0
    brier = logloss = 0.0
    idx = {"H": 0, "D": 1, "A": 2}
    for r in rows:
        p = [float(r["p_home"]), float(r["p_draw"]) * multiplier, float(r["p_away"])]
        total = sum(p)
        p = [v / total for v in p]
        y = idx[r["actual_result"]]
        pred = max(range(3), key=lambda i: p[i])
        correct += pred == y
        actual_draws += y == 1
        predicted_draws += pred == 1
        draw_tp += pred == 1 and y == 1
        brier += sum((p[i] - (1.0 if i == y else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(1e-15, p[y]))
    n = len(rows)
    return {
        "n": n, "accuracy": correct / n if n else None,
        "actual_draws": actual_draws, "predicted_draws": predicted_draws,
        "draw_recall": draw_tp / actual_draws if actual_draws else None,
        "draw_precision": draw_tp / predicted_draws if predicted_draws else None,
        "brier": brier / n if n else None, "logloss": logloss / n if n else None,
    }


def main():
    pred_rows = read_prediction_rows()
    frozen = read_frozen()
    # Chronological split only for a diagnostic calibration check. The first 350
    # rows choose a single draw multiplier by multiclass log loss; the last 150
    # are a strictly held-out evaluation and are never used to select it.
    train, holdout = frozen[:350], frozen[350:]
    best = None
    for step in range(20, 121):
        multiplier = step / 40.0  # 0.50 to 3.00, increments 0.025
        metrics = score_metrics(train, multiplier)
        candidate = (metrics["logloss"], multiplier, metrics)
        if best is None or candidate[0] < best[0]:
            best = candidate
    payload = {
        "prediction_file": str(PREDICTIONS),
        "frozen_file": str(FROZEN),
        "prediction_handicap_mapping_audit": {
            "n": len(pred_rows),
            "consistent": sum(r["consistent"] for r in pred_rows),
            "inconsistent": [r for r in pred_rows if not r["consistent"]],
            "limitation": "Recovered handicap values are not equivalent to an independently verified official Sporttery ticket snapshot."
        },
        "frozen_500_draw_diagnostic": {
            "sample_n": len(frozen),
            "split": {"train_rows": len(train), "holdout_rows": len(holdout), "chronological": True},
            "train_baseline": score_metrics(train),
            "train_selected_multiplier_by_logloss": best[1],
            "train_adjusted": best[2],
            "holdout_baseline": score_metrics(holdout),
            "holdout_adjusted": score_metrics(holdout, best[1]),
            "acceptance_rule": "Reject adjustment if holdout Brier or LogLoss worsens; classifier metrics alone are not enough."
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
