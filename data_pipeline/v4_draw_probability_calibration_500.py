import csv,json,sqlite3,math,sys
sys.path.insert(0,"data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_draw_probability_calibration_500.csv"
SUM="model_validation/v4_draw_probability_calibration_500_summary.json"
CUTOFF="2026-08-22T00:00:00"
HIST=15

con=sqlite3.connect(DB)
with open(MAP,encoding="utf-8-sig") as f: mp=list(csv.DictReader(f))
assert len(mp)==500 and len({x["match_id"] for x in mp})==500

train=list(con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND m.kickoff < ?
ORDER BY m.kickoff""",(CUTOFF,)))
db={r[0]:r for r in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL""")}

cm.HIST_LIMIT=HIST

# Keep the already-validated single correction fixed; only the draw calibration is new.
away_err=[]
for r in train:
    lh,la=cm.model_lambdas(con,"v4",r)
    away_err.append(la-r[5])
away_bias=sum(away_err)/len(away_err)

def probs(r):
    lh,la=cm.model_lambdas(con,"v4",r)
    la=max(0.05,la-away_bias)
    m=cm.matrix(lh,la,True)
    pH=sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j)
    pD=sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i==j)
    pA=sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j)
    return [pH,pD,pA]

# Chronological calibration split inside the pre-cutoff pool.
split=max(1,int(len(train)*0.70))
cal=train[split:]

def outcome(r):
    return 0 if r[4]>r[5] else 1 if r[4]==r[5] else 2

cal_p=[probs(r) for r in cal]
cal_y=[outcome(r) for r in cal]

best=None
for k in [0.50+i*0.05 for i in range(91)]:
    ll=0.0
    for p,y in zip(cal_p,cal_y):
        q=[p[0],p[1]*k,p[2]]
        s=sum(q); q=[x/s for x in q]
        ll-=math.log(max(1e-12,q[y]))
    ll/=len(cal_y)
    if best is None or ll<best[0]:
        best=(ll,k)

draw_mult=best[1]

def pred(r):
    p=probs(r)
    q=[p[0],p[1]*draw_mult,p[2]]
    s=sum(q); q=[x/s for x in q]
    pred=max(range(3),key=lambda i:q[i])
    y=outcome(r)
    return "HDA"[pred], "HDA"[y], q

rows=[]
for z in mp:
    r=db[z["match_id"]]
    b=pred(r)
    rows.append({"sample_row":z["sample_row"],"match_id":r[0],"kickoff":r[1],"home":r[2],"away":r[3],
                 "actual":b[1],"corrected_pred":b[0],"pH":b[2][0],"pD":b[2][1],"pA":b[2][2]})

correct=sum(x["actual"]==x["corrected_pred"] for x in rows)
counts={c:sum(x["corrected_pred"]==c for x in rows) for c in "HDA"}
actuals={c:sum(x["actual"]==c for x in rows) for c in "HDA"}

summary={"experiment":"V4 draw probability calibration on top of 15-window + away-lambda bias correction",
"production_status":"EXPERIMENT_ONLY","n":500,"train_matches_before_cutoff":len(train),
"calibration_matches":len(cal),"hist_window":HIST,"estimated_away_lambda_bias":away_bias,
"draw_probability_multiplier":draw_mult,"calibration_logloss":best[0],
"baseline_correct":256,"baseline_accuracy":0.512,"corrected_correct":correct,
"corrected_accuracy":correct/500,"improvement_correct":correct-256,"improvement_accuracy":(correct-256)/500,
"predicted_counts":counts,"actual_counts":actuals}

with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
with open(SUM,"w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False,indent=2))
