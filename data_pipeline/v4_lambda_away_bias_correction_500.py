import csv,json,sqlite3,math,sys
sys.path.insert(0,"data_pipeline")
import compare_models as cm
DB="football_model_database.sqlite"; MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_lambda_away_bias_correction_500.csv"; SUM="model_validation/v4_lambda_away_bias_correction_500_summary.json"
con=sqlite3.connect(DB)
with open(MAP,encoding="utf-8-sig") as f: mp=list(csv.DictReader(f))
assert len(mp)==500 and len({x["match_id"] for x in mp})==500
db={r[0]:r for r in con.execute("SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.status='finished' AND r.ft_home IS NOT NULL")}
# Correction is estimated only from matches before frozen cutoff, not from the frozen 500.
cutoff="2026-08-22T00:00:00"
train=list(con.execute("SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.status='finished' AND r.ft_home IS NOT NULL AND m.kickoff < ? ORDER BY m.kickoff",(cutoff,)))
cm.HIST_LIMIT=15
errs=[]
for r in train:
    lh,la=cm.model_lambdas(con,"v4",r)
    errs.append(la-r[5])
bias=sum(errs)/len(errs)
# Apply only the one selected module: subtract pre-cutoff away-lambda bias.
def pred(r,corrected):
    lh,la=cm.model_lambdas(con,"v4",r)
    if corrected: la=max(0.05,la-bias)
    m=cm.matrix(lh,la,True)
    p={"H":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j),"D":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i==j),"A":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j)}
    y="H" if r[4]>r[5] else "D" if r[4]==r[5] else "A"
    return max(p,key=p.get),y,lh,la
rows=[]
for z in mp:
    r=db[z["match_id"]]; b=pred(r,False); c=pred(r,True)
    rows.append({"sample_row":z["sample_row"],"match_id":r[0],"kickoff":r[1],"home":r[2],"away":r[3],"actual":b[1],"baseline_pred":b[0],"corrected_pred":c[0],"lambda_home":c[2],"lambda_away_before":b[3],"lambda_away_after":c[3]})
base=sum(x["baseline_pred"]==x["actual"] for x in rows); new=sum(x["corrected_pred"]==x["actual"] for x in rows)
summary={"experiment":"V4 single-module away lambda bias correction","production_status":"EXPERIMENT_ONLY","n":500,"train_matches_before_cutoff":len(train),"cutoff":cutoff,"hist_window":15,"estimated_away_lambda_bias":bias,"baseline_correct":base,"baseline_accuracy":base/500,"corrected_correct":new,"corrected_accuracy":new/500,"improvement_correct":new-base,"improvement_accuracy":(new-base)/500}
with open(OUT,"w",encoding="utf-8",newline="") as f:
 w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
with open(SUM,"w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False,indent=2))