"""One-factor-at-a-time V5.3 tail ablation on the sourced 16-match replay.

No fitting: fixed sensitivity multipliers are diagnostic only. Closing odds do
not have exact provider event timestamps; this is not a strict pre-kick replay.
"""
from __future__ import annotations
import argparse, copy, csv, json, math
from model_validation.v5_2_market_lambda import predict_from_markets
from model_validation.v5_3_direct_matrix_repair import DEFAULT_WEIGHTS, build_score_matrix

TOTAL_KEYS = ("0", "1", "2", "3", "4", "5", "6", "7", "8+")
MARGIN_KEYS = ("0", "1", "2", "3", "4", "5+")
AWAY_KEYS = ("0", "1", "2", "3", "4+")

def outcome(h, a):
    return "H" if h > a else "D" if h == a else "A"

def scenarios():
    base = copy.deepcopy(DEFAULT_WEIGHTS)
    out = {"baseline_v5_3": base}
    # Aggregate 5+ sensitivity: diagnostic only, not a proposed production setting.
    for factor in (1.15, 1.25, 1.40, 1.60, 1.80, 2.00, 2.50, 3.00, 4.00):
        w = copy.deepcopy(base)
        for k in ("5", "6", "7", "8+"):
            w["total_goals"][k] = factor
        out[f"total_tail_5plus_x{factor:.2f}"] = w
    # Separate exactly-five from 6+ because the observed tail may be misallocated.
    for factor in (1.50, 2.00, 2.50, 3.00, 4.00, 4.50, 5.00):
        w = copy.deepcopy(base)
        w["total_goals"]["5"] = factor
        out[f"exactly_5_goals_x{factor:.2f}"] = w
    for factor in (0.50, 0.75, 1.25, 1.50):
        w = copy.deepcopy(base)
        for k in ("6", "7", "8+"):
            w["total_goals"][k] = factor
        out[f"total_6plus_x{factor:.2f}"] = w
    for factor in (1.15, 1.25, 1.40):
        w = copy.deepcopy(base)
        for k in ("2", "3+"):
            w["away_goals"][k] = factor
        out[f"away_tail_x{factor:.2f}"] = w
    for factor in (1.15, 1.25, 1.40):
        w = copy.deepcopy(base)
        for k in ("0-0", "1-1", "2-2"):
            w["draw_exact"][k] = factor
        out[f"draw_only_x{factor:.2f}"] = w
    return out
def score_variant(rows, weights, label):
    per_match = []
    for item in rows:
        base = predict_from_markets(
            over_odds=float(item["over_odds"]), under_odds=float(item["under_odds"]),
            goal_line=float(item["goal_line"]), home_odds=float(item["home_odds"]),
            draw_odds=float(item["draw_odds"]), away_odds=float(item["away_odds"]),
            max_goals=15)
        m = base["market"]
        mat = build_score_matrix(
            lambda_home=float(base["lambda_home"]), lambda_away=float(base["lambda_away"]),
            market_home_probability=float(m["p_home_devig"]),
            market_draw_probability=float(m["p_draw_devig"]),
            market_away_probability=float(m["p_away_devig"]),
            min_goals=15, weights=weights)
        h, a = int(item["actual_home"]), int(item["actual_away"])
        actual = outcome(h, a)
        probs = [mat["p_home"], mat["p_draw"], mat["p_away"]]
        pick = ("H", "D", "A")[max(range(3), key=lambda i: probs[i])]
        bins, margins, away_bins = mat["total_goal_bins"], mat["absolute_net_goal_bins"], mat["away_goal_bins"]
        top = mat["top_scores"]
        per_match.append({
            "actual": actual, "probs": probs, "pick": pick,
            "top1_hit": top[0]["home"] == h and top[0]["away"] == a,
            "top2_hit": top[1]["home"] == h and top[1]["away"] == a,
            "total": h+a, "actual_margin": abs(h-a), "actual_away": a,
            "bins": bins, "margins": margins, "away_bins": away_bins,
            "p5plus": sum(bins[k] for k in ("5","6","7","8+")),
            "top1": top[0]["score"], "top2": top[1]["score"],
            "mass": mat["probability_mass"],
        })
    n = len(per_match)
    brier = sum(sum((r["probs"][i] - (1.0 if r["actual"] == cls else 0.0))**2
                    for i, cls in enumerate(("H","D","A"))) for r in per_match) / n
    logloss = -sum(math.log(max(r["probs"][("H","D","A").index(r["actual"])], 1e-15))
                   for r in per_match) / n
    actual_total = {k: 0 for k in TOTAL_KEYS}
    actual_margin = {k: 0 for k in MARGIN_KEYS}
    actual_away = {k: 0 for k in AWAY_KEYS}
    for r in per_match:
        actual_total[str(r["total"]) if r["total"] <= 7 else "8+"] += 1
        actual_margin[str(r["actual_margin"]) if r["actual_margin"] <= 4 else "5+"] += 1
        actual_away[str(r["actual_away"]) if r["actual_away"] <= 3 else "4+"] += 1
    mean = lambda key, keys: {k: sum(r[key][k] for r in per_match)/n for k in keys}
    total_goal_brier = sum(sum((r["bins"][k] - (1.0 if (str(r["total"]) if r["total"] <= 7 else "8+") == k else 0.0))**2 for k in TOTAL_KEYS) for r in per_match) / n
    total_goal_logloss = -sum(math.log(max(r["bins"][str(r["total"]) if r["total"] <= 7 else "8+"], 1e-15)) for r in per_match) / n
    binary_tail_brier = sum((r["p5plus"] - (1.0 if r["total"] >= 5 else 0.0))**2 for r in per_match) / n
    binary_tail_logloss = -sum(math.log(max(r["p5plus"] if r["total"] >= 5 else 1.0-r["p5plus"], 1e-15)) for r in per_match) / n
    return {
        "scenario": label, "matches": n,
        "direction_hits": sum(r["pick"] == r["actual"] for r in per_match),
        "draw_recall": (sum(r["pick"] == "D" and r["actual"] == "D" for r in per_match)
                        / max(1, sum(r["actual"] == "D" for r in per_match))),
        "actual_draws": sum(r["actual"] == "D" for r in per_match),
        "predicted_draw_argmax": sum(r["pick"] == "D" for r in per_match),
        "top1_score_hits": sum(r["top1_hit"] for r in per_match),
        "top2_score_hits": sum(r["top2_hit"] for r in per_match),
        "brier": brier, "logloss": logloss,
        "total_goals_9class_brier": total_goal_brier,
        "total_goals_9class_logloss": total_goal_logloss,
        "binary_5plus_brier": binary_tail_brier,
        "binary_5plus_logloss": binary_tail_logloss,
        "actual_5plus_rate": sum(r["total"] >= 5 for r in per_match)/n,
        "mean_predicted_5plus": sum(r["p5plus"] for r in per_match)/n,
        "mean_predicted_total_bins": mean("bins", TOTAL_KEYS),
        "actual_total_bins_count": actual_total,
        "mean_predicted_abs_margin_bins": mean("margins", MARGIN_KEYS),
        "actual_abs_margin_bins_count": actual_margin,
        "mean_predicted_away_goal_bins": mean("away_bins", AWAY_KEYS),
        "actual_away_goal_bins_count": actual_away,
        "all_matrices_normalized": all(abs(r["mass"]-1.0) < 1e-10 for r in per_match),
        "matches_detail": [{"top1":r["top1"],"top2":r["top2"],"p5plus":r["p5plus"],
                            "actual_total":r["total"],"pick":r["pick"],"actual":r["actual"]}
                           for r in per_match],
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="model_validation/fixtures/v5_3_unl_20261114_15.csv")
    args = ap.parse_args()
    with open(args.input, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    results = [score_variant(rows, w, name) for name, w in scenarios().items()]
    print(json.dumps({
        "scope": "16 UEFA Nations League matches, 2026-11-14/15",
        "odds_basis": "Bet365 archived closing odds; exact provider event timestamp unavailable",
        "replay_status": "RETROSPECTIVE_CLOSE_ODDS_NOT_STRICT_PREKICK_TIMESTAMPED",
        "parameter_policy": "PRESET_SENSITIVITY_ONLY_NO_FITTING_NO_TRAINING",
        "warning": "16 matches are diagnostic only; do not select a production setting from this sample",
        "results": results
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
