"""Chronological holdout test: baseline Poisson vs V5.2 goal-tail shadow.

Requires immutable, verified pre-kickoff market/lineup snapshot CSV with final
scores. No missing market values are imputed. The last chronological fraction
is held out; the experimental multipliers are fixed before evaluation.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime
from pathlib import Path

from model_validation.v5_2_market_lambda import poisson_pmf
from model_validation.v5_2_goal_tail_shadow import reweight_score_grid

INPUT = Path(os.environ.get("TAIL_BACKTEST_CSV", "data/market_lambda_backtest.csv"))
OUT = Path(os.environ.get("TAIL_BACKTEST_OUT", "model_validation/v5_2_goal_tail_backtest_report.json"))
REQUIRED = {
    "match_id", "kickoff_utc", "snapshot_utc", "snapshot_type",
    "home_goals", "away_goals", "lambda_home", "lambda_away",
    "ou_line", "over_odds", "under_odds",
}
BUCKETS = ("0-1", "2", "3-4", "5-6", "7+")


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamps must include timezone")
    return dt


def brier(probs, actual_index):
    return sum((p - (1.0 if i == actual_index else 0.0)) ** 2 for i, p in enumerate(probs))


def logloss(prob):
    return -math.log(max(1e-15, min(1.0, prob)))


def bucket(total):
    return "0-1" if total <= 1 else "2" if total == 2 else "3-4" if total <= 4 else "5-6" if total <= 6 else "7+"


def distribution(lh, la, max_goals=15):
    hp, ap = poisson_pmf(lh, max_goals), poisson_pmf(la, max_goals)
    return [(h, a, ph * pa) for h, ph in enumerate(hp) for a, pa in enumerate(ap)]


def summarize(rows):
    if not rows:
        return {"n": 0, "status": "BLOCKED_NO_ELIGIBLE_ROWS"}
    scores = {"baseline": [], "tail": []}
    hda = {"baseline": [], "tail": []}
    bins = {"baseline": [], "tail": []}
    tail5 = {"baseline": [], "tail": []}
    exact = {"baseline": 0, "tail": 0}
    for r in rows:
        hg, ag = int(r["home_goals"]), int(r["away_goals"])
        total = hg + ag
        actual_bucket = BUCKETS.index(bucket(total))
        actual_hda = 0 if hg > ag else 1 if hg == ag else 2
        base_grid = distribution(float(r["lambda_home"]), float(r["lambda_away"]))
        tail_result = reweight_score_grid(base_grid)
        tail_grid = [(x["home"], x["away"], x["probability"]) for x in tail_result["scorelines"]]
        for name, grid in (("baseline", base_grid), ("tail", tail_grid)):
            mass = sum(p for _, _, p in grid)
            if mass <= 0:
                raise ValueError("invalid distribution mass")
            grid = [(h, a, p / mass) for h, a, p in grid]
            bp = {b: 0.0 for b in BUCKETS}
            hp = [0.0, 0.0, 0.0]
            for h, a, p in grid:
                bp[bucket(h + a)] += p
                hp[0 if h > a else 1 if h == a else 2] += p
            ranked = sorted(grid, key=lambda x: x[2], reverse=True)
            bins[name].append(brier([bp[b] for b in BUCKETS], actual_bucket))
            scores[name].append(logloss(bp[bucket(total)]))
            hda[name].append(brier(hp, actual_hda))
            tail5[name].append((sum(p for h, a, p in grid if h + a >= 5) - (1.0 if total >= 5 else 0.0)) ** 2)
            exact[name] += int((ranked[0][0], ranked[0][1]) == (hg, ag))
    n = len(rows)
    result = {"n": n, "status": "EVALUATED"}
    for name in ("baseline", "tail"):
        result[name] = {
            "goal_bucket_brier": sum(bins[name]) / n,
            "realized_goal_bucket_log_loss": sum(scores[name]) / n,
            "hda_brier": sum(hda[name]) / n,
            "five_plus_goals_brier": sum(tail5[name]) / n,
            "exact_top_score_hit_rate": exact[name] / n,
        }
    result["delta_tail_minus_baseline"] = {
        metric: result["tail"][metric] - result["baseline"][metric]
        for metric in result["baseline"]
    }
    result["interpretation"] = (
        "Negative deltas are improvements for Brier/log-loss; positive delta is improvement for exact-score hit rate."
    )
    result["promotion_gate"] = (
        "Do not promote unless holdout goal-bucket Brier and log-loss do not worsen, "
        "five-plus-goal Brier improves, and results replicate on a second untouched time window."
    )
    return result


def load_csv(path):
    if not path.exists():
        return [], [f"missing input: {path}"]
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = set(reader.fieldnames or [])
    missing = REQUIRED - columns
    if missing:
        return [], [f"missing required columns: {','.join(sorted(missing))}"]
    eligible, rejected = [], []
    seen = set()
    for i, r in enumerate(rows, start=2):
        try:
            if not r["match_id"] or r["match_id"] in seen:
                raise ValueError("missing or duplicate match_id")
            seen.add(r["match_id"])
            if parse_dt(r["snapshot_utc"]) >= parse_dt(r["kickoff_utc"]):
                raise ValueError("snapshot is not strictly pre-kickoff")
            if r["snapshot_type"].lower() not in {"prematch", "pre_match", "赛前", "赛前快照"}:
                raise ValueError("not a pre-match snapshot")
            nums = ["home_goals", "away_goals", "lambda_home", "lambda_away", "ou_line", "over_odds", "under_odds"]
            vals = {k: float(r[k]) for k in nums}
            if any(not math.isfinite(v) for v in vals.values()):
                raise ValueError("non-finite numeric value")
            if vals["home_goals"] < 0 or vals["away_goals"] < 0 or int(vals["home_goals"]) != vals["home_goals"] or int(vals["away_goals"]) != vals["away_goals"]:
                raise ValueError("invalid final score")
            if vals["lambda_home"] < 0 or vals["lambda_away"] < 0:
                raise ValueError("negative lambda")
            if vals["over_odds"] <= 1 or vals["under_odds"] <= 1:
                raise ValueError("invalid decimal odds")
            eligible.append(r)
        except Exception as exc:
            rejected.append(f"row {i}: {exc}")
    eligible.sort(key=lambda x: parse_dt(x["kickoff_utc"]))
    return eligible, rejected


def main():
    rows, rejected = load_csv(INPUT)
    report = {
        "input": str(INPUT),
        "eligible_rows": len(rows),
        "rejected_rows": len(rejected),
        "rejection_reasons": rejected[:100],
        "data_policy": "No odds, lambda, or score values are reconstructed or imputed.",
        "status": "BLOCKED_INSUFFICIENT_REAL_HISTORICAL_DATA",
    }
    if len(rows) >= 30:
        split = max(1, int(len(rows) * 0.70))
        report["chronological_split"] = {
            "earlier_rows": split,
            "holdout_rows": len(rows) - split,
            "holdout_start_kickoff_utc": rows[split]["kickoff_utc"] if split < len(rows) else None,
        }
        report["earlier_window_diagnostic"] = summarize(rows[:split])
        report["holdout"] = summarize(rows[split:])
        report["status"] = "EVALUATED" if len(rows) - split >= 10 else "BLOCKED_HOLDOUT_TOO_SMALL"
    else:
        report["required_minimum_rows"] = 30
        report["note"] = "Need >=30 verified chronological rows, with >=10 in holdout; a larger holdout is strongly preferred."
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"].startswith("BLOCKED"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
