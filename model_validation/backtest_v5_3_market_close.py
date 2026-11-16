"""Reproducible V5.3 retrospective replay using archived Bet365 closing odds.

This is NOT a strict timestamped pre-kickoff replay: the archive labels prices as
closing prices but does not expose an exact provider event timestamp for each
price. Do not write these rows through the production trace API or synthesize
prediction timestamps. This script calls the same V5.2 market-lambda and V5.3
matrix functions as the candidate engine, then scores against final results.
"""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path
from model_validation.v5_2_market_lambda import predict_from_markets
from model_validation.v5_3_direct_matrix_repair import build_score_matrix

def outcome(h: int, a: int) -> str:
    return "H" if h > a else "D" if h == a else "A"

def brier(rows, key):
    total = 0.0
    for r in rows:
        p = r[key]
        actual = r["actual"]
        total += sum((p[i] - (1.0 if cls == actual else 0.0)) ** 2 for i, cls in enumerate(("H", "D", "A")))
    return total / len(rows)

def logloss(rows, key):
    return -sum(math.log(max(r[key][("H", "D", "A").index(r["actual"])], 1e-15)) for r in rows) / len(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="model_validation/fixtures/v5_3_unl_20261114_15.csv")
    args = ap.parse_args()
    rows = []
    with open(args.input, newline="", encoding="utf-8") as f:
        for item in csv.DictReader(f):
            base = predict_from_markets(
                over_odds=float(item["over_odds"]), under_odds=float(item["under_odds"]),
                goal_line=float(item["goal_line"]), home_odds=float(item["home_odds"]),
                draw_odds=float(item["draw_odds"]), away_odds=float(item["away_odds"]),
                max_goals=15,
            )
            m = base["market"]
            matrix = build_score_matrix(
                lambda_home=float(base["lambda_home"]), lambda_away=float(base["lambda_away"]),
                market_home_probability=float(m["p_home_devig"]),
                market_draw_probability=float(m["p_draw_devig"]),
                market_away_probability=float(m["p_away_devig"]), min_goals=15,
            )
            ah, aa = int(item["actual_home"]), int(item["actual_away"])
            actual = outcome(ah, aa)
            v53 = [matrix["p_home"], matrix["p_draw"], matrix["p_away"]]
            market = [m["p_home_devig"], m["p_draw_devig"], m["p_away_devig"]]
            top = matrix["top_scores"]
            bins = matrix["total_goal_bins"]
            rows.append({
                "date": item["date"], "home": item["home"], "away": item["away"],
                "actual_score": f"{ah}-{aa}", "actual": actual,
                "top1": top[0]["score"], "top1_probability": top[0]["probability"],
                "top2": top[1]["score"], "top2_probability": top[1]["probability"],
                "v53_hda": v53, "market_hda": market,
                "v53_pick": ("H", "D", "A")[max(range(3), key=lambda i: v53[i])],
                "lambda_home": base["lambda_home"], "lambda_away": base["lambda_away"],
                "draw_exact": matrix["draw_exact_probabilities"],
                "goal_bins": bins,
                "p_total_5_plus": sum(bins[k] for k in ("5", "6", "7", "8+")),
                "score1_hit": top[0]["home"] == ah and top[0]["away"] == aa,
                "score2_hit": top[1]["home"] == ah and top[1]["away"] == aa,
                "direction_hit": ("H", "D", "A")[max(range(3), key=lambda i: v53[i])] == actual,
                "actual_5_plus": ah + aa >= 5,
                "source_url": item["source_url"],
            })
            assert abs(matrix["probability_mass"] - 1.0) < 1e-10
    n = len(rows)
    summary = {
        "n": n,
        "v53_direction_hits": sum(r["direction_hit"] for r in rows),
        "v53_top1_score_hits": sum(r["score1_hit"] for r in rows),
        "v53_top2_score_hits": sum(r["score2_hit"] for r in rows),
        "actual_draws": sum(r["actual"] == "D" for r in rows),
        "v53_draw_argmax": sum(r["v53_pick"] == "D" for r in rows),
        "actual_5_plus": sum(r["actual_5_plus"] for r in rows),
        "mean_v53_5_plus_probability": sum(r["p_total_5_plus"] for r in rows) / n,
        "v53_brier": brier(rows, "v53_hda"),
        "market_brier": brier(rows, "market_hda"),
        "v53_logloss": logloss(rows, "v53_hda"),
        "market_logloss": logloss(rows, "market_hda"),
        "probability_mass_all_valid": True,
        "odds_basis": "Bet365 archived closing prices",
        "exact_provider_event_timestamp_available": False,
        "status": "RETROSPECTIVE_CLOSE_ODDS_REPLAY_NOT_TIMESTAMP_VERIFIED",
    }
    print(json.dumps({"summary": summary, "matches": rows}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
