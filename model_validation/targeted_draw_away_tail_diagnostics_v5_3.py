"""V5.3 targeted diagnostics: draw class, away-goal tail, score concentration, goal-tail allocation.

Retrospective diagnostic only. Uses archived closing odds in the 16-match fixture;
no timestamp fabrication, fitting, production writes, or V5.1 changes.
"""
from __future__ import annotations
import copy, csv, json, math
from collections import Counter
from model_validation.v5_2_market_lambda import predict_from_markets
from model_validation.v5_3_direct_matrix_repair import DEFAULT_WEIGHTS, build_score_matrix

DRAW_KEYS = ("0-0", "1-1", "2-2")
GOAL_KEYS = tuple(str(i) for i in range(8)) + ("8+",)
AWAY_KEYS = tuple(str(i) for i in range(4)) + ("4+",)

def result_class(h, a):
    return "H" if h > a else "D" if h == a else "A"

def bucket(n, cap):
    return str(n) if n <= cap else f"{cap}+"

def market_input(row):
    return predict_from_markets(
        over_odds=float(row["over_odds"]), under_odds=float(row["under_odds"]),
        goal_line=float(row["goal_line"]), home_odds=float(row["home_odds"]),
        draw_odds=float(row["draw_odds"]), away_odds=float(row["away_odds"]),
        max_goals=15)

def summarize(matrix, row, market):
    ah, aa = int(row["actual_home"]), int(row["actual_away"])
    mass = sum(map(sum, matrix))
    hda = {"H": 0.0, "D": 0.0, "A": 0.0}
    totals = {k: 0.0 for k in GOAL_KEYS}
    away = {k: 0.0 for k in AWAY_KEYS}
    fav_goal = Counter()
    fav = "H" if market["p_home_devig"] > market["p_away_devig"] else "A"
    cells = []
    for h, line in enumerate(matrix):
        for a, p in enumerate(line):
            hda[result_class(h, a)] += p
            totals[bucket(h+a, 7)] += p
            away[bucket(a, 3)] += p
            if fav == "H":
                fav_goal[bucket(a, 3)] += p
            else:
                fav_goal[bucket(h, 3)] += p
            cells.append((p, h, a))
    cells.sort(reverse=True)
    draw_exact = {f"{g}-{g}": matrix[g][g] for g in (0,1,2)}
    draw_three = sum(draw_exact.values())
    draw_other = hda["D"] - draw_three
    pick = max(hda, key=hda.get)
    actual = result_class(ah, aa)
    actual_total = ah+aa
    actual_fav_goals = aa if fav == "H" else ah
    return {
        "date": row["date"], "match": row["home"]+"-"+row["away"],
        "actual_score": f"{ah}-{aa}", "actual_result": actual,
        "market_favourite": fav, "home_is_favourite": fav == "H",
        "p_home": hda["H"], "p_draw": hda["D"], "p_away": hda["A"],
        "p_draw_0_0": draw_exact["0-0"], "p_draw_1_1": draw_exact["1-1"],
        "p_draw_2_2": draw_exact["2-2"], "p_draw_0_0_1_1_2_2": draw_three,
        "p_other_draw": draw_other, "draw_is_argmax": pick == "D",
        "direction_pick": pick, "direction_hit": pick == actual,
        "top1": f"{cells[0][1]}-{cells[0][2]}",
        "top1_probability": cells[0][0], "top2": f"{cells[1][1]}-{cells[1][2]}",
        "top1_is_draw": cells[0][1] == cells[0][2],
        "top1_is_1_1": cells[0][1:3] == (1,1),
        "p_away_goals_0": away["0"], "p_away_goals_1": away["1"],
        "p_away_goals_2": away["2"], "p_away_goals_3plus": away["3+"],
        "p_underdog_goals_2plus": sum(v for k,v in fav_goal.items() if k in ("2","3+","4+")),\n        "p_underdog_goals_0": fav_goal["0"], "p_underdog_goals_1": fav_goal["1"],\n        "p_underdog_goals_2": fav_goal["2"], "p_underdog_goals_3plus": fav_goal["3+"],\n        "p_5plus_favourite_win": tail_by_fav_outcome["favourite_win"],\n        "p_5plus_underdog_win": tail_by_fav_outcome["underdog_win"],\n        "p_5plus_draw": tail_by_fav_outcome["draw"],
        "actual_favourite_conceded": actual_fav_goals,
        "actual_total_goals": actual_total,
        "p_total_5plus": sum(totals[k] for k in ("5","6","7","8+")),
        "total_goal_probs": totals, "mass": mass,
        "market_hda": [market["p_home_devig"],market["p_draw_devig"],market["p_away_devig"]],
    }

def build(row, base, draw_factor=1.0, tail_factor=None, away_factor=1.0):
    weights = copy.deepcopy(DEFAULT_WEIGHTS)
    # Apply multipliers relative to current V5.3 defaults, not replace them.
    for k in DRAW_KEYS:
        weights["draw_exact"][k] *= draw_factor
    if tail_factor is not None:
        for k in ("5","6","7","8+"):
            weights["total_goals"][k] *= tail_factor
    # Isolate away-goal tail only in home-market-favourite matches.
    weights["away_goals"]["2"] *= away_factor
    weights["away_goals"]["3+"] *= away_factor
    m = base["market"]
    return build_score_matrix(
        lambda_home=float(base["lambda_home"]), lambda_away=float(base["lambda_away"]),
        market_home_probability=float(m["p_home_devig"]),
        market_draw_probability=float(m["p_draw_devig"]),
        market_away_probability=float(m["p_away_devig"]),
        min_goals=15, weights=weights)

def evaluate(rows, draw_factor=1.0, tail_factor=None, away_factor=1.0):
    records=[]
    for row in rows:
        base=market_input(row)
        mat=build(row,base,draw_factor,tail_factor,away_factor)["score_probability_matrix"]
        records.append(summarize(mat,row,base["market"]))
    n=len(records)
    actual_draws=sum(r["actual_result"]=="D" for r in records)
    top11=sum(r["top1_is_1_1"] for r in records)
    top_draw=sum(r["top1_is_draw"] for r in records)
    actual5=sum(r["actual_total_goals"]>=5 for r in records)
    return {
        "matches":n, "draws_actual":actual_draws,
        "draw_argmax_count":sum(r["draw_is_argmax"] for r in records),
        "draw_recall":sum(r["draw_is_argmax"] and r["actual_result"]=="D" for r in records)/max(1,actual_draws),
        "mean_p_draw":sum(r["p_draw"] for r in records)/n,
        "mean_p_0_0":sum(r["p_draw_0_0"] for r in records)/n,
        "mean_p_1_1":sum(r["p_draw_1_1"] for r in records)/n,
        "mean_p_2_2":sum(r["p_draw_2_2"] for r in records)/n,
        "mean_p_0_0_1_1_2_2":sum(r["p_draw_0_0_1_1_2_2"] for r in records)/n,
        "top1_draw_count":top_draw, "top1_1_1_count":top11,
        "actual_5plus_count":actual5,
        "mean_predicted_5plus":sum(r["p_total_5plus"] for r in records)/n,\n        "mean_5plus_favourite_win_mass":sum(r["p_5plus_favourite_win"] for r in records)/n,\n        "mean_5plus_underdog_win_mass":sum(r["p_5plus_underdog_win"] for r in records)/n,\n        "mean_5plus_draw_mass":sum(r["p_5plus_draw"] for r in records)/n,
        "home_favourite_matches":sum(r["home_is_favourite"] for r in records),
        "home_favourite_mean_underdog_2plus":(
            sum(r["p_underdog_goals_2plus"] for r in records if r["home_is_favourite"]) /
            max(1,sum(r["home_is_favourite"] for r in records))),
        "home_favourite_actual_underdog_2plus":sum(
            r["actual_favourite_conceded"]>=2 for r in records if r["home_is_favourite"]),
        "all_matrices_normalized":all(abs(r["mass"]-1.0)<1e-10 for r in records),
        "details":records,
    }

def main():
    with open("model_validation/fixtures/v5_3_unl_20261114_15.csv",newline="",encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    scenarios=[
        ("current_v5_3",1.0,None,1.0),
        ("draw_only_x1_25",1.25,None,1.0),
        ("draw_only_x1_50",1.50,None,1.0),
        ("draw_only_x2_00",2.00,None,1.0),
        ("tail_only_x2_50",1.0,2.50,1.0),
        ("away_tail_only_x1_25",1.0,None,1.25),
        ("away_tail_only_x1_50",1.0,None,1.50),
        ("draw1_25_tail2_50",1.25,2.50,1.0),
        ("draw1_25_tail2_50_away1_25",1.25,2.50,1.25),
    ]
    out=[]
    for name,d,t,a in scenarios:
        result=evaluate(rows,d,t,a)
        result["scenario"]=name
        result["draw_multiplier"]=d
        result["tail_multiplier"]=t
        result["away_goal_multiplier"]=a
        out.append(result)
    print(json.dumps({
        "scope":"16 UEFA Nations League matches, 2026-11-14/15",
        "odds_basis":"Bet365 archived closing odds; exact provider event timestamps unavailable",
        "status":"RETROSPECTIVE_DIAGNOSTIC_NOT_STRICT_PREKICK_NOT_PRODUCTION",
        "interpretation":"Draw factors multiply only the three exact draw cells; tail factors multiply total-goal 5/6/7/8+ bins; away factors multiply away-goal 2/3+ cells only when home side is market favourite.",
        "warning":"Small diagnostic sample; do not select production parameters from this sample.",
        "scenarios":out
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
