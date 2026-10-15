#!/usr/bin/env python3
"""Leakage-safe V5.2 goal-distribution calibration experiment.

Reads the frozen 448-row merged backtest CSV. Does not modify model code or
historical predictions. Fits one common lambda multiplier on the earlier
chronological 70% only, then compares baseline vs candidate on the later 30%.
The candidate is NOT production-approved unless held-out scoreline NLL and
goal-bin Brier both improve.
"""
import csv, json, math, os
from collections import Counter
from pathlib import Path

INPUT = os.getenv(
    "V52_GOAL_AUDIT_INPUT",
    "model_validation/v52_jincai_20260901_20261013/v52_plus_nations_league_448_merged_backtest_20261013.csv",
)
OUTPUT = os.getenv(
    "V52_GOAL_AUDIT_OUTPUT",
    "model_validation/v52_jincai_20260901_20261013/v52_goal_distribution_repair_audit.json",
)
MIN_SCALE = 0.80
MAX_SCALE = 1.50
STEP = 0.025
BINS = ((0, 1, "0-1"), (2, 3, "2-3"), (4, 5, "4-5"), (6, 99, "6+"))


def poisson_pmf(k, lam):
    return math.exp(-lam + k * math.log(max(lam, 1e-12)) - math.lgamma(k + 1))


def score_nll(rows, scale):
    total = 0.0
    for r in rows:
        lh = max(0.05, float(r["lambda_home"]) * scale)
        la = max(0.05, float(r["lambda_away"]) * scale)
        hg, ag = int(r["actual_home_goals"]), int(r["actual_away_goals"])
        total -= math.log(max(1e-12, poisson_pmf(hg, lh) * poisson_pmf(ag, la)))
    return total / max(1, len(rows))


def total_goal_bin_probs(lh, la):
    # Sum of independent Poisson variables is Poisson(lh + la).
    lam = lh + la
    p = [poisson_pmf(k, lam) for k in range(0, 40)]
    p0_1 = p[0] + p[1]
    p2_3 = p[2] + p[3]
    p4_5 = p[4] + p[5]
    p6p = max(0.0, 1.0 - p0_1 - p2_3 - p4_5)
    z = p0_1 + p2_3 + p4_5 + p6p
    return [v / z for v in (p0_1, p2_3, p4_5, p6p)]


def metrics(rows, scale):
    nll = 0.0
    total_abs_error = 0.0
    brier = 0.0
    actual_bins = Counter()
    predicted_total = 0.0
    for r in rows:
        lh = max(0.05, float(r["lambda_home"]) * scale)
        la = max(0.05, float(r["lambda_away"]) * scale)
        hg, ag = int(r["actual_home_goals"]), int(r["actual_away_goals"])
        goals = hg + ag
        nll -= math.log(max(1e-12, poisson_pmf(hg, lh) * poisson_pmf(ag, la)))
        total_abs_error += abs((lh + la) - goals)
        predicted_total += lh + la
        probs = total_goal_bin_probs(lh, la)
        actual_idx = next(i for i, (lo, hi, _) in enumerate(BINS) if lo <= goals <= hi)
        actual_bins[BINS[actual_idx][2]] += 1
        brier += sum((probs[i] - (1.0 if i == actual_idx else 0.0)) ** 2 for i in range(4))
    n = max(1, len(rows))
    return {
        "n": len(rows),
        "lambda_scale": round(scale, 4),
        "scoreline_nll": round(nll / n, 6),
        "total_goals_mae_using_lambda_sum": round(total_abs_error / n, 6),
        "mean_predicted_total_goals": round(predicted_total / n, 6),
        "mean_actual_total_goals": round(sum(int(r["actual_home_goals"]) + int(r["actual_away_goals"]) for r in rows) / n, 6),
        "goal_bin_brier": round(brier / n, 6),
        "actual_goal_bins": {label: actual_bins.get(label, 0) for _, _, label in BINS},
    }


def main():
    with open(INPUT, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    required = {"match_id", "kickoff", "sample_source", "actual_home_goals", "actual_away_goals", "lambda_home", "lambda_away"}
    if not rows or not required.issubset(rows[0]):
        raise SystemExit(f"INPUT_SCHEMA_INVALID required={sorted(required)}")
    merged_rows = rows
    native_ids = {r["match_id"] for r in merged_rows if r["match_id"]}
    if len(merged_rows) != 448 or len(native_ids) != 398:
        raise SystemExit(f"FROZEN_SAMPLE_INTEGRITY_FAIL rows={len(merged_rows)} native_ids={len(native_ids)}")
    # Nations League retrospective rows have no saved lambda_home/lambda_away.
    # They cannot be scored in a lambda-scaling experiment; exclude them explicitly.
    rows = [
        r for r in merged_rows
        if r["match_id"]
        and r["lambda_home"].strip()
        and r["lambda_away"].strip()
        and r["actual_home_goals"].strip()
        and r["actual_away_goals"].strip()
    ]
    if len(rows) != 398 or len({r["match_id"] for r in rows}) != 398:
        raise SystemExit(f"SCORABLE_NATIVE_SAMPLE_INTEGRITY_FAIL rows={len(rows)}")
    rows.sort(key=lambda r: (r["kickoff"], r["sample_source"], r["home_team"], r["away_team"]))
    split = int(len(rows) * 0.70)
    train, test = rows[:split], rows[split:]
    if len(train) < 100 or len(test) < 50:
        raise SystemExit("INSUFFICIENT_CHRONOLOGICAL_SPLIT")
    scales = []
    x = MIN_SCALE
    while x <= MAX_SCALE + 1e-9:
        scales.append(round(x, 3))
        x += STEP
    fit = [{"scale": s, "train_scoreline_nll": score_nll(train, s)} for s in scales]
    best = min(fit, key=lambda v: v["train_scoreline_nll"])
    baseline = metrics(test, 1.0)
    candidate = metrics(test, best["scale"])
    approved = (
        candidate["scoreline_nll"] < baseline["scoreline_nll"]
        and candidate["goal_bin_brier"] < baseline["goal_bin_brier"]
    )
    out = {
        "title": "V5.2 goal-distribution repair audit",
        "input": INPUT,
        "sample_integrity": {"merged_rows": len(merged_rows), "scored_native_rows": len(rows), "excluded_nations_league_rows_missing_lambdas": len(merged_rows) - len(rows), "unique_native_ids": len(native_ids)},
        "method": "chronological 70/30 split; fit one common lambda multiplier on train scoreline NLL only; evaluate later 30% holdout",
        "train": {"n": len(train), "best_scale": best["scale"], "best_scoreline_nll": round(best["train_scoreline_nll"], 6)},
        "holdout_baseline": baseline,
        "holdout_candidate": candidate,
        "candidate_accepted_for_next_experiment": approved,
        "guardrails": [
            "This is an isolated experiment; no production model or historical prediction was overwritten.",
            "Common scaling is not automatically applied to V5.2; candidate needs held-out scoreline NLL and goal-bin Brier improvement.",
            "The 50 Nations League retrospective rows are excluded from lambda fitting/scoring because their saved lambda inputs are blank; no result is claimed for those 50 rows.",
            "Poisson likelihood is a baseline diagnostic and does not model red-card or match-state tail dependence."
        ],
        "scale_grid_train": fit,
    }
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: out[k] for k in ("sample_integrity", "train", "holdout_baseline", "holdout_candidate", "candidate_accepted_for_next_experiment")}, ensure_ascii=False, indent=2))
    print("REPORT=" + OUTPUT)


if __name__ == "__main__":
    main()

# Acceptance trigger: rerun isolated audit workflow on the frozen sample.
