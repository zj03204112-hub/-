import csv, json, math, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
import sys
sys.path.insert(0, "data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_draw_probability_matrix_diagnostic_500.json"
DETAIL="model_validation/v4_draw_probability_matrix_diagnostic_500.csv"

def auc_binary(vals_pos, vals_neg):
    # Mann-Whitney formulation; returns NaN if one class is absent.
    pairs=0; wins=0.0
    for x in vals_pos:
        for y in vals_neg:
            pairs += 1
            if x>y: wins += 1
            elif x==y: wins += .5
    return wins/pairs if pairs else float("nan")

def quantiles(xs):
    xs=sorted(xs)
    if not xs: return {}
    def q(p):
        k=(len(xs)-1)*p; lo=int(math.floor(k)); hi=int(math.ceil(k))
        return xs[lo] if lo==hi else xs[lo]+(xs[hi]-xs[lo])*(k-lo)
    return {k:round(q(p),6) for k,p in [("q10",.10),("q25",.25),("q50",.50),("q75",.75),("q90",.90)]}

def row_stats(m):
    total=sum(sum(r) for r in m)
    diag=sum(m[i][i] for i in range(cm.N))
    low=sum(m[i][j] for i in range(3) for j in range(3))
    lowdraw=m[0][0]+m[1][1]+m[2][2]
    return {
        "p_draw_matrix":diag,
        "p_00":m[0][0],"p_11":m[1][1],"p_22":m[2][2],
        "p_low_3x3":low,"p_low_draw_00_11_22":lowdraw,
        "p_low_non_draw":low-lowdraw,
        "p_score_00_11_share_of_draw":lowdraw/diag if diag>0 else 0,
        "matrix_sum":total
    }

con=sqlite3.connect(DB)
with open(MAP,encoding="utf-8-sig",newline="") as f: mapping=list(csv.DictReader(f))
if len(mapping)!=500 or len({r["match_id"] for r in mapping})!=500 or any(r["mapping_status"]!="unique" for r in mapping):
    raise RuntimeError("FROZEN_MAPPING_NOT_STRICT_500")
by_id={r[0]:r for r in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL""")}
rows=[]
for r in mapping:
    m=by_id[r["match_id"]]
    p,_=cm.run_model(con,"v4",m)
    lh,la=cm.model_lambdas(con,"v4",m)
    mat=cm.matrix(lh,la,True)
    s=row_stats(mat)
    total=lh+la; diff=abs(lh-la)
    actual="H" if m[4]>m[5] else "D" if m[4]==m[5] else "A"
    rows.append({
        "sample_row":int(r["sample_row"]),"date":r["date"],"league":r["league"],
        "home":r["home"],"away":r["away"],"actual":actual,
        "home_score":m[4],"away_score":m[5],
        "p_home":p["H"],"p_draw":p["D"],"p_away":p["A"],
        "lambda_home":lh,"lambda_away":la,"lambda_total":total,
        "lambda_abs_diff":diff,"draw_minus_max_non_draw":p["D"]-max(p["H"],p["A"]),
        "score_mode_prob":max(max(x) for x in mat),
        **s
    })

groups={
    "draw": [x for x in rows if x["actual"]=="D"],
    "non_draw": [x for x in rows if x["actual"]!="D"],
    "home": [x for x in rows if x["actual"]=="H"],
    "away": [x for x in rows if x["actual"]=="A"]
}
metrics={}
for g,items in groups.items():
    metrics[g]={
        "n":len(items),
        "p_draw":quantiles([x["p_draw"] for x in items]),
        "lambda_total":quantiles([x["lambda_total"] for x in items]),
        "lambda_abs_diff":quantiles([x["lambda_abs_diff"] for x in items]),
        "p_00":quantiles([x["p_00"] for x in items]),
        "p_11":quantiles([x["p_11"] for x in items]),
        "p_22":quantiles([x["p_22"] for x in items]),
        "p_low_3x3":quantiles([x["p_low_3x3"] for x in items]),
        "p_low_draw_00_11_22":quantiles([x["p_low_draw_00_11_22"] for x in items]),
        "draw_minus_max_non_draw":quantiles([x["draw_minus_max_non_draw"] for x in items])
    }

auc={}
for field in ["p_draw","p_00","p_11","p_22","p_low_3x3","p_low_draw_00_11_22","lambda_total","lambda_abs_diff"]:
    auc[field]=auc_binary([x[field] for x in groups["draw"]],[x[field] for x in groups["non_draw"]])

# Threshold diagnostics: how many actual draws would be captured at different raw-P(D) cutoffs.
thresholds=[]
for t in [0.20,0.22,0.24,0.26,0.28,0.30,0.32,0.34,0.36,0.38,0.40,0.42,0.45,0.50]:
    pred=[x for x in rows if x["p_draw"]>=t]
    hits=sum(x["actual"]=="D" for x in pred)
    thresholds.append({"threshold":t,"pred_draw_n":len(pred),"draw_hits":hits,
                       "draw_recall":hits/len(groups["draw"]),"draw_precision":hits/len(pred) if pred else 0})

# Calibration by raw P(D) bins.
bins=[]
edges=[0,.10,.15,.20,.25,.30,.35,.40,.45,.50,1.01]
for lo,hi in zip(edges[:-1],edges[1:]):
    a=[x for x in rows if lo<=x["p_draw"]<hi]
    if a:
        bins.append({"lo":lo,"hi":hi,"n":len(a),"actual_draw_rate":sum(x["actual"]=="D" for x in a)/len(a),
                     "mean_p_draw":sum(x["p_draw"] for x in a)/len(a)})

# Identify the actual draws with the strongest raw draw signal, and draws with weakest signal.
draw_sorted=sorted(groups["draw"],key=lambda x:x["p_draw"],reverse=True)
weak_draws=sorted(groups["draw"],key=lambda x:x["p_draw"])[:20]
strong_draws=draw_sorted[:20]

# Score-matrix rank diagnostics for actual score and draw cells.
score_rank=[]
for x in rows:
    mat=cm.matrix(x["lambda_home"],x["lambda_away"],True)
    actual_cell=mat[x["home_score"]][x["away_score"]] if x["home_score"]<cm.N and x["away_score"]<cm.N else 0
    diag=max(mat[i][i] for i in range(cm.N))
    flat=sorted([(mat[i][j],i,j) for i in range(cm.N) for j in range(cm.N)],reverse=True)
    rank=1+next((k for k,v in enumerate(flat) if v[1]==x["home_score"] and v[2]==x["away_score"]),len(flat))
    x["actual_score_prob"]=actual_cell; x["actual_score_rank"]=rank
    if x["actual"]=="D":
        x["actual_draw_cell_prob"]=mat[x["home_score"]][x["away_score"]]
        x["draw_cell_rank"]=rank

summary={
    "n":500,"actual_draws":len(groups["draw"]),"actual_non_draws":len(groups["non_draw"]),
    "auc_draw_vs_non_draw":auc,
    "group_quantiles":metrics,
    "raw_p_draw_thresholds":thresholds,
    "raw_p_draw_calibration_bins":bins,
    "top20_actual_draws_by_raw_p_draw":strong_draws,
    "bottom20_actual_draws_by_raw_p_draw":weak_draws,
    "draw_cell_examples":sorted([x for x in rows if x["actual"]=="D"],key=lambda x:x["actual_draw_cell_prob"],reverse=True)[:20],
    "overall_mean":{"p_draw":sum(x["p_draw"] for x in rows)/500,
                    "actual_draw_rate":len(groups["draw"])/500,
                    "lambda_total":sum(x["lambda_total"] for x in rows)/500}
}
with open(OUT,"w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)
fields=list(rows[0].keys())
with open(DETAIL,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
print(json.dumps({"output":OUT,"detail":DETAIL,"n":500,"actual_draws":len(groups["draw"]),"auc_p_draw":auc["p_draw"],"mean_p_draw":summary["overall_mean"]["p_draw"],"actual_draw_rate":summary["overall_mean"]["actual_draw_rate"]},ensure_ascii=False))

# trigger diagnostic run
