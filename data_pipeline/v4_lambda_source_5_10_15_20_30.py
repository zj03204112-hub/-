import csv, json, math, sqlite3, os, sys
from datetime import datetime, timedelta
sys.path.insert(0,"data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_lambda_source_5_10_15_20_30_500.csv"
SUMMARY="model_validation/v4_lambda_source_5_10_15_20_30_summary.json"
WINDOWS=[5,10,15,20,30]

def actual(r):
    return "H" if r[4]>r[5] else "D" if r[4]==r[5] else "A"

def prob(m):
    return {"H":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j),
            "D":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i==j),
            "A":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j)}

def score_metrics(pred, actuals):
    n=len(pred)
    return {
      "mae":sum(abs(a-p) for p,a in zip(pred,actuals))/n,
      "rmse":math.sqrt(sum((a-p)**2 for p,a in zip(pred,actuals))/n),
      "bias":sum(p-a for p,a in zip(pred,actuals))/n,
      "actual_mean":sum(actuals)/n,
      "pred_mean":sum(pred)/n
    }

def auc(pos,neg,key):
    w=n=0
    for a in pos:
        for b in neg:
            x=key(a); y=key(b); n+=1
            w += 1 if x>y else .5 if x==y else 0
    return w/n if n else None

con=sqlite3.connect(DB)
with open(MAP,encoding="utf-8-sig") as f: mp=list(csv.DictReader(f))
assert len(mp)==500 and len({x["match_id"] for x in mp})==500 and all(x["mapping_status"]=="unique" for x in mp)
db={r[0]:r for r in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL""")}
rows=[]
summary={"experiment":"V4 lambda source decomposition; frozen 500; windows 5/10/15/20/30",
         "production_status":"EXPERIMENT_ONLY","n":500,"windows":{}}

# Preserve current setting and restore it after the experiment.
original_limit=cm.HIST_LIMIT

for win in WINDOWS:
    cm.HIST_LIMIT=win
    recs=[]
    for z in mp:
        r=db[z["match_id"]]
        lh,la=cm.model_lambdas(con,"v4",r)
        m=cm.matrix(lh,la,True); p=prob(m); a=actual(r)
        rec={"window":win,"sample_row":int(z["sample_row"]),"match_id":r[0],"kickoff":r[1],
             "home":r[2],"away":r[3],"actual":a,"home_score":r[4],"away_score":r[5],
             "lambda_home":lh,"lambda_away":la,"lambda_total":lh+la,"lambda_abs_diff":abs(lh-la),
             "pred":max(p,key=p.get),"pH":p["H"],"pD":p["D"],"pA":p["A"],
             "home_lambda_error":lh-r[4],"away_lambda_error":la-r[5],
             "abs_home_lambda_error":abs(lh-r[4]),"abs_away_lambda_error":abs(la-r[5])}
        recs.append(rec); rows.append(rec)

    home_mae=sum(x["abs_home_lambda_error"] for x in recs)/500
    away_mae=sum(x["abs_away_lambda_error"] for x in recs)/500
    home_bias=sum(x["home_lambda_error"] for x in recs)/500
    away_bias=sum(x["away_lambda_error"] for x in recs)/500
    correct=sum(x["pred"]==x["actual"] for x in recs)
    draws=sum(x["actual"]=="D" for x in recs)
    pred_draws=sum(x["pred"]=="D" for x in recs)
    draw_correct=sum(x["pred"]=="D" and x["actual"]=="D" for x in recs)
    draw_auc=auc([x for x in recs if x["actual"]=="D"],[x for x in recs if x["actual"]!="D"],lambda x:x["pD"])
    summary["windows"][str(win)]={
      "n":500,"correct":correct,"accuracy":correct/500,
      "home_lambda":{"mae":home_mae,"rmse":math.sqrt(sum(x["home_lambda_error"]**2 for x in recs)/500),"bias":home_bias},
      "away_lambda":{"mae":away_mae,"rmse":math.sqrt(sum(x["away_lambda_error"]**2 for x in recs)/500),"bias":away_bias},
      "combined_lambda_mae":(home_mae+away_mae)/2,
      "combined_lambda_rmse":math.sqrt(sum(x["home_lambda_error"]**2+x["away_lambda_error"]**2 for x in recs)/(1000)),
      "actual_draws":draws,"predicted_draws":pred_draws,"draw_correct":draw_correct,
      "draw_auc":draw_auc
    }

# Source ablation at the best lambda-MAE window.
best_window=min(WINDOWS,key=lambda w:summary["windows"][str(w)]["combined_lambda_mae"])
cm.HIST_LIMIT=best_window

def v4_variant(con,match,variant):
    mid,kickoff,home,away,fh,fa=match
    ko=datetime.fromisoformat(kickoff[:19]); cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
    # Base historical rows.
    hh=cm.hist(con,home,cutoff); ah=cm.hist(con,away,cutoff)
    def dyn(team_hist, opponent_hist):
        if not team_hist:return (1.15,1.15)
        w=[math.exp(-0.045*i) for i in range(len(team_hist))] if variant!="no_recency" else [1.0]*len(team_hist)
        sw=sum(w); att=de=0.0
        for i,((gf,ga),opp) in enumerate(team_hist):
            ogf,oga=cm.rates(cm.hist(con,opp,cutoff))
            if variant=="no_opp_quality":
                opp_def=1.0; opp_att=1.0
            else:
                opp_def=max(.75,oga/1.35); opp_att=max(.75,ogf/1.35)
            att += (gf/opp_def)*w[i]; de += (ga/opp_att)*w[i]
        return max(.2,att/sw),max(.2,de/sw)
    hgf,hga=dyn(hh,None); agf,aga=dyn(ah,None)
    if variant!="no_strength":
        for side,os in (("home",cm.strength(con,home,cutoff)),("away",cm.strength(con,away,cutoff))):
            os=max(.75,min(2.25,os)); factor=max(.94,min(1.06,(os/1.5)**.18))
            if side=="home": hgf*=factor; hga/=factor
            else: agf*=factor; aga/=factor
    # isolate home advantage / linear lambda transform
    if variant=="no_home_adv":
        lh=max(.15,min(4.0,.58*hgf+.30*aga))
    else:
        lh=max(.15,min(4.0,.65+.58*hgf+.30*aga))
    la=max(.12,min(3.5,.58+.58*agf+.30*hga))
    return lh,la

variants=["baseline","no_recency","no_opp_quality","no_strength","no_home_adv"]
summary["source_ablation"]={"window":best_window,"variants":{}}
match_list=[db[z["match_id"]] for z in mp]
for variant in variants:
    recs=[]
    for r in match_list:
        lh,la=v4_variant(con,r,variant)
        p=prob(cm.matrix(lh,la,True)); a=actual(r)
        recs.append((lh,la,r[4],r[5],max(p,key=p.get),a,p))
    hm=score_metrics([x[0] for x in recs],[x[2] for x in recs])
    am=score_metrics([x[1] for x in recs],[x[3] for x in recs])
    acc=sum(x[4]==x[5] for x in recs)/500
    summary["source_ablation"]["variants"][variant]={
      "accuracy":acc,
      "home_lambda_mae":hm["mae"],"home_lambda_bias":hm["bias"],
      "away_lambda_mae":am["mae"],"away_lambda_bias":am["bias"],
      "combined_lambda_mae":(hm["mae"]+am["mae"])/2
    }

summary["best_window_by_lambda_mae"]=best_window
summary["largest_source_signal"]=min(summary["source_ablation"]["variants"],
    key=lambda v:summary["source_ablation"]["variants"][v]["combined_lambda_mae"])
# Improvement of removing each component: positive means component was hurting lambda MAE.
base=summary["source_ablation"]["variants"]["baseline"]["combined_lambda_mae"]
for v,d in summary["source_ablation"]["variants"].items():
    d["mae_change_vs_baseline"]=d["combined_lambda_mae"]-base
    d["mae_improvement_if_removed"]=base-d["combined_lambda_mae"]

# Restore.
cm.HIST_LIMIT=original_limit

with open(OUT,"w",encoding="utf-8",newline="") as f:
    fields=list(rows[0].keys()); w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
with open(SUMMARY,"w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False,indent=2))
