#!/usr/bin/env python3
"""Read-only classification-path diagnosis for the frozen 500-row V4 sample."""
import csv, json, math
from pathlib import Path

SRC = Path("model_validation/500_match_V4_frozen_predictions.csv")
OUT = Path("reviews/2026-10-12/frozen500_classification_path.json")
LABELS = ("H", "D", "A")

def main():
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 500:
        raise SystemExit(f"Expected frozen 500 rows, got {len(rows)}")
    counts = {k: 0 for k in ("argmax_draw", "stored_draw", "argmax_label_mismatch",
                              "draw_gt_home", "draw_gt_away", "probability_sum_bad")}
    max_draw_gap = -1.0
    min_draw_gap = 1.0
    examples = []
    sum_probs = {k: 0.0 for k in LABELS}
    draw_actual = {"n": 0, "p_draw_sum": 0.0, "p_draw_max": 0.0}
    nondraw_actual = {"n": 0, "p_draw_sum": 0.0}
    confusion = {a: {p: 0 for p in LABELS} for a in LABELS}
    for r in rows:
        p = {"H": float(r["p_home"]), "D": float(r["p_draw"]), "A": float(r["p_away"])}
        actual = r["actual_result"]
        # Stable tie order matches Python max over insertion order H,D,A.
        pred = max(p, key=p.get)
        counts["argmax_draw"] += pred == "D"
        counts["stored_draw"] += r["pred_result"] == "D"
        counts["argmax_label_mismatch"] += pred != r["pred_result"]
        counts["draw_gt_home"] += p["D"] > p["H"]
        counts["draw_gt_away"] += p["D"] > p["A"]
        counts["probability_sum_bad"] += abs(sum(p.values()) - 1.0) > 2e-5
        for k in LABELS: sum_probs[k] += p[k]
        if actual == "D":
            draw_actual["n"] += 1
            draw_actual["p_draw_sum"] += p["D"]
            draw_actual["p_draw_max"] = max(draw_actual["p_draw_max"], p["D"])
        else:
            nondraw_actual["n"] += 1
            nondraw_actual["p_draw_sum"] += p["D"]
        confusion[actual][pred] += 1
        gap = p["D"] - max(p["H"], p["A"])
        max_draw_gap = max(max_draw_gap, gap)
        min_draw_gap = min(min_draw_gap, gap)
        if len(examples) < 12 and gap > -0.12:
            examples.append({"sample_row": r["sample_row"], "date": r["date"],
                "home": r["home"], "away": r["away"], "p_home": p["H"],
                "p_draw": p["D"], "p_away": p["A"], "draw_minus_best_non_draw": round(gap, 6),
                "actual": actual, "pred": pred})
    n = len(rows)
    out = {
      "status": "diagnostic_only_no_model_or_sample_mutation",
      "source": str(SRC), "n": n, "counts": counts,
      "mean_probabilities": {k: round(v/n, 6) for k,v in sum_probs.items()},
      "draw_actual": {"n": draw_actual["n"],
        "mean_p_draw": round(draw_actual["p_draw_sum"]/max(1,draw_actual["n"]), 6),
        "max_p_draw": round(draw_actual["p_draw_max"], 6)},
      "non_draw_actual": {"n": nondraw_actual["n"],
        "mean_p_draw": round(nondraw_actual["p_draw_sum"]/max(1,nondraw_actual["n"]), 6)},
      "draw_minus_best_non_draw_gap": {"max": round(max_draw_gap, 6), "min": round(min_draw_gap, 6)},
      "confusion_actual_rows_predicted_columns": confusion,
      "near_draw_decision_examples": examples,
      "interpretation": [
        "If stored predictions equal argmax labels but draw never wins argmax, label mapping is not the failure point.",
        "p_draw beating one side but never both indicates a probability-ranking/score-distribution issue upstream of the final argmax.",
        "This CSV does not contain lambda_home/lambda_away or the full score matrix; lambda-level root cause cannot be proven from this file alone."
      ]
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
if __name__ == "__main__": main()
