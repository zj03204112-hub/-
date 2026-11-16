"""Joint V5.3 direction calibration + score-matrix validation.

The direction adjustment is applied to the *same joint score matrix*: within each
H/D/A class, scoreline probabilities are scaled proportionally so that the class
marginals become a fixed convex blend of the matrix and de-vigged market H/D/A.
No fitting/training is performed. All blend/tail factors are predeclared sensitivity
settings; the 16-match archived-close sample is diagnostic, not out-of-sample proof.
"""
from __future__ import annotations
import copy, csv, json, math
from model_validation.v5_2_market_lambda import predict_from_markets
from model_validation.v5_3_direct_matrix_repair import DEFAULT_WEIGHTS, build_score_matrix

CLASSES = ("H", "D", "A")
TOTAL_KEYS = tuple(str(i) for i in range(8)) + ("8+",)
MARGIN_KEYS = tuple(str(i) for i in range(5)) + ("5+",)
AWAY_KEYS = tuple(str(i) for i in range(4)) + ("4+",)

def cls(h, a):
    return "H" if h > a else "D" if h == a else "A"

def blend_matrix_marginals(matrix_result, market, alpha):
    """Rake all score cells within each result class to blended H/D/A marginals."""
    if not 0 <= alpha <= 1:
        raise ValueError("alpha must be in [0,1]")
    old = [matrix_result["p_home"], matrix_result["p_draw"], matrix_result["p_away"]]
    target = [(1-alpha)*old[i] + alpha*market[i] for i in range(3)]
    cells = matrix_result["score_probability_matrix"]
    sums = [0.0, 0.0, 0.0]
    for h, row in enumerate(cells):
        for a, p in enumerate(row):
            sums[CLASSES.index(cls(h,a))] += p
    out = [[0.0 for _ in row] for row in cells]
    for h, row in enumerate(cells):
        for a, p in enumerate(row):
            i = CLASSES.index(cls(h,a))
            out[h][a] = p * target[i] / sums[i] if sums[i] > 0 else 0.0
    return out, target

def summarize(matrix, actual_h, actual_a):
    nmax = len(matrix)-1
    p = [0.0,0.0,0.0]
    totals = {k:0.0 for k in TOTAL_KEYS}
    margins = {k:0.0 for k in MARGIN_KEYS}
    away = {k:0.0 for k in AWAY_KEYS}
    ranked = []
    for h,row in enumerate(matrix):
        for a,v in enumerate(row):
            p[CLASSES.index(cls(h,a))] += v
            totals[str(h+a) if h+a <= 7 else "8+"] += v
            margins[str(abs(h-a)) if abs(h-a) <= 4 else "5+"] += v
            away[str(a) if a <= 3 else "4+"] += v
            ranked.append((v,h,a))
    ranked.sort(reverse=True)
    total = actual_h+actual_a
    actual_class = cls(actual_h,actual_a)
    pick = CLASSES[max(range(3),key=lambda i:p[i])]
    p5 = sum(totals[k] for k in ("5","6","7","8+"))
    return {
        "hda":p, "pick":pick, "actual":actual_class,
        "direction_hit":pick==actual_class,
        "draw_hit":pick=="D" and actual_class=="D",
        "top1":f"{ranked[0][1]}-{ranked[0][2]}",
        "top2":f"{ranked[1][1]}-{ranked[1][2]}",
        "top1_hit":ranked[0][1:]==(actual_h,actual_a),
        "top2_hit":ranked[1][1:]==(actual_h,actual_a),
        "total_bins":totals,"margin_bins":margins,"away_bins":away,
        "p5plus":p5,"actual_total":total,
        "actual_total_bin":str(total) if total<=7 else "8+",
        "actual_margin_bin":str(abs(actual_h-actual_a)) if abs(actual_h-actual_a)<=4 else "5+",
        "actual_away_bin":str(actual_a) if actual_a<=3 else "4+",
        "mass":sum(map(sum,matrix))
    }

def score(rows, alpha, tail_factor, name):
    details=[]
    for item in rows:
        base=predict_from_markets(
            over_odds=float(item["over_odds"]),under_odds=float(item["under_odds"]),
            goal_line=float(item["goal_line"]),home_odds=float(item["home_odds"]),
            draw_odds=float(item["draw_odds"]),away_odds=float(item["away_odds"]),max_goals=15)
        m=base["market"]
        weights=copy.deepcopy(DEFAULT_WEIGHTS)
        if tail_factor is not None:
            for k in ("5","6","7","8+"):
                weights["total_goals"][k]=tail_factor
        raw=build_score_matrix(
            lambda_home=float(base["lambda_home"]),lambda_away=float(base["lambda_away"]),
            market_home_probability=float(m["p_home_devig"]),
            market_draw_probability=float(m["p_draw_devig"]),
            market_away_probability=float(m["p_away_devig"]),min_goals=15,weights=weights)
        market=[m["p_home_devig"],m["p_draw_devig"],m["p_away_devig"]]
        mat,target=blend_matrix_marginals(raw,market,alpha)
        r=summarize(mat,int(item["actual_home"]),int(item["actual_away"]))
        r.update({"date":item["date"],"home":item["home"],"away":item["away"],
                  "actual_score":f'{item["actual_home"]}-{item["actual_away"]}',
                  "target_hda":target,"market_hda":market})
        details.append(r)
    n=len(details)
    brier=sum(sum((r["hda"][i]-(1 if r["actual"]==c else 0))**2 for i,c in enumerate(CLASSES)) for r in details)/n
    logloss=-sum(math.log(max(r["hda"][CLASSES.index(r["actual"])],1e-15)) for r in details)/n
    actual_draws=sum(r["actual"]=="D" for r in details)
    actual5=sum(r["actual_total"]>=5 for r in details)
    tail_brier=sum((r["p5plus"]-(1 if r["actual_total"]>=5 else 0))**2 for r in details)/n
    tail_logloss=-sum(math.log(max(r["p5plus"] if r["actual_total"]>=5 else 1-r["p5plus"],1e-15)) for r in details)/n
    mean_bins={k:sum(r["total_bins"][k] for r in details)/n for k in TOTAL_KEYS}
    actual_bins={k:sum(r["actual_total_bin"]==k for r in details) for k in TOTAL_KEYS}
    mean_margin={k:sum(r["margin_bins"][k] for r in details)/n for k in MARGIN_KEYS}
    actual_margin={k:sum(r["actual_margin_bin"]==k for r in details) for k in MARGIN_KEYS}
    mean_away={k:sum(r["away_bins"][k] for r in details)/n for k in AWAY_KEYS}
    actual_away={k:sum(r["actual_away_bin"]==k for r in details) for k in AWAY_KEYS}
    return {
      "scenario":name,"matches":n,"direction_hits":sum(r["direction_hit"] for r in details),
      "draw_recall":sum(r["draw_hit"] for r in details)/max(1,actual_draws),
      "actual_draws":actual_draws,"predicted_draw_argmax":sum(r["pick"]=="D" for r in details),
      "brier":brier,"logloss":logloss,"top1_score_hits":sum(r["top1_hit"] for r in details),
      "top2_score_hits":sum(r["top2_hit"] for r in details),
      "actual_5plus_rate":actual5/n,"mean_predicted_5plus":sum(r["p5plus"] for r in details)/n,
      "binary_5plus_brier":tail_brier,"binary_5plus_logloss":tail_logloss,
      "mean_total_goal_bins":mean_bins,"actual_total_goal_bins_count":actual_bins,
      "mean_absolute_margin_bins":mean_margin,"actual_absolute_margin_bins_count":actual_margin,
      "mean_away_goal_bins":mean_away,"actual_away_goal_bins_count":actual_away,
      "all_matrices_normalized":all(abs(r["mass"]-1)<1e-10 for r in details),
      "details":details
    }

def main():
    path="model_validation/fixtures/v5_3_unl_20261114_15.csv"
    with open(path,newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f))
    scenarios=[
      (0.0,None,"baseline_v5_3"),
      (0.0,2.50,"tail_only_x2.50"),
      (0.25,None,"direction_market_blend_25pct"),
      (0.50,None,"direction_market_blend_50pct"),
      (0.75,None,"direction_market_blend_75pct"),
      (1.00,None,"market_direction_anchor_100pct"),
      (0.25,2.50,"joint_direction25_tail2.50"),
      (0.50,2.50,"joint_direction50_tail2.50"),
      (0.75,2.50,"joint_direction75_tail2.50"),
    ]
    results=[score(rows,a,t,name) for a,t,name in scenarios]
    print(json.dumps({
      "scope":"16 Nations League fixtures, 2026-11-14/15",
      "odds_basis":"Bet365 archived closing odds; exact provider event timestamp unavailable",
      "status":"RETROSPECTIVE_CLOSE_ODDS_DIAGNOSTIC_NOT_STRICT_PREKICK",
      "method":"fixed H/D/A market blend applied by class-wise score-matrix raking; tail factor applied before raking",
      "parameter_policy":"predeclared sensitivity only; no training or fitting",
      "warning":"Do not select production parameters from this 16-match sample; V5.1 remains unchanged",
      "results":results
    },ensure_ascii=False,indent=2))

if __name__=="__main__": main()
