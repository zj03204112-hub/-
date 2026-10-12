#!/usr/bin/env python3
"""Read-only audit of the frozen V4 500-match classification path.

Does not modify predictions, frozen samples, model code, or V5.1.
"""
import csv
import json
import math
from pathlib import Path

SOURCE = Path("model_validation/500_match_V4_frozen_predictions.csv")
OUT = Path("reviews/2026-10-11/classification_path_audit_500.json")
LABELS = ("H", "D", "A")


def main():
    with SOURCE.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    counts = {k: 0 for k in LABELS}
    actual_draw_confusion = {k: 0 for k in LABELS}
    label_mismatches = []
    margins = []
    sums = {"p_home": 0.0, "p_draw": 0.0, "p_away": 0.0}
    actual_counts = {k: 0 for k in LABELS}
    for row in rows:
        p = {"H": float(row["p_home"]), "D": float(row["p_draw"]), "A": float(row["p_away"])}
        # Match compare_models.py: max(p, key=p.get), whose insertion order is H,D,A.
        predicted = max(p, key=p.get)
        counts[predicted] += 1
        actual = row["actual_result"]
        actual_counts[actual] += 1
        if actual == "D":
            actual_draw_confusion[predicted] += 1
        if predicted != row["pred_result"]:
            label_mismatches.append({
                "sample_row": int(row["sample_row"]),
                "stored": row["pred_result"],
                "recomputed": predicted,
                "probabilities": p,
            })
        margins.append(p["D"] - max(p["H"], p["A"]))
        for key, field in (("p_home", "p_home"), ("p_draw", "p_draw"), ("p_away", "p_away")):
            sums[key] += float(row[field])
    margins_sorted = sorted(margins)
    n = len(rows)
    result = {
        "source": str(SOURCE),
        "n": n,
        "actual_counts": actual_counts,
        "predicted_counts_recomputed_from_probabilities": counts,
        "predicted_counts_stored": {
            k: sum(row["pred_result"] == k for row in rows) for k in LABELS
        },
        "actual_draws_predicted_as": actual_draw_confusion,
        "mean_probabilities": {k: v / n for k, v in sums.items()} if n else {},
        "draw_minus_best_non_draw_margin": {
            "min": margins_sorted[0],
            "p10": margins_sorted[int((n - 1) * 0.10)],
            "median": margins_sorted[int((n - 1) * 0.50)],
            "p90": margins_sorted[int((n - 1) * 0.90)],
            "max": margins_sorted[-1],
            "count_draw_is_argmax": sum(x > 0 for x in margins),
        } if n else {},
        "stored_label_vs_argmax_mismatches": label_mismatches,
        "interpretation": (
            "If mismatch count is zero and draw argmax count is zero, the immediate failure is not "
            "a probability-column/label serialization mismatch: the stored classifier faithfully "
            "uses argmax, but the generated probability vectors never rank D first. This audit alone "
            "does not establish whether lambda generation, score-matrix aggregation, or the model "
            "specification is the ultimate cause."
        ),
        "read_only": True,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
