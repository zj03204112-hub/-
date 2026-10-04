import csv,json,sqlite3,math,sys
sys.path.insert(0,"data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_lambda_gap_shrink_500.csv"
SUM="model_validation/v4_lambda_gap_shrink_500_summary.json"
CUTOFF="2026-08-22T00:00:00"
HIST_WINDOW=15

con=sqlite3.connect(DB)
with open(MAP,encoding="utf-8-sig") as f:
    mp=list(csv.DictReader(f))
assert len(mp)==500
assert len({x["match_id"] for x in mp})==500

# Fixed component from the 51.2% baseline: the existing away-lambda bias correction.
train=list(con.execute("""
SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL
AND m.kickoff < ? ORDER BY m.kickoff
""",(CUTOFF,)))
cm.HIST_LIMIT=HIST_WINDOW
away_errors=[]
for r in train:
    lh,la=cm.model_lambdas(con,"v4",r)
    away_errors.append(la-r[5])
away_bias=sum(away_errors)/len(away_errors)

def probs(lh,la):
    m=cm.matrix(lh,la,True)
    return {
        "H":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j),
        "D":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i==j),
        "A":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j)
    }

def corrected_lambdas(r):
    lh,la=cm.model_lambdas(con,"v4",r)
    return lh,max(0.05,la-away_bias)

def shrink(lh,la,s):
    mid=(lh+la)/2.0
    return max(0.05,mid+s*(lh-mid)),max(0.05,mid+s*(la-mid))

def outcome(r):
    return "H" if r[4]>r[5] else "D" if r[4]==r[5] else "A"

# Chronological calibration only: frozen 500 is never used to choose s.
split=int(len(train)*0.70)
cal=train[split:]
grid=[round(x,2) for x in [0.50+i*0.05 for i in range(11)]]
best_s=1.0
best_ll=float("inf")
cal_rows=0
for s in grid:
    ll=0.0
    n=0
    for r in cal:
        lh,la=corrected_lambdas(r)
        lh2,la2=shrink(lh,la,s)
        p=probs(lh2,la2)
        y=outcome(r)
        ll-=math.log(max(p[y],1e-12))
        n+=1
    ll/=n
    if ll<best_ll:
        best_ll=ll
        best_s=s
        cal_rows=n

db={r[0]:r for r in con.execute("""
SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL
""")}

rows=[]
base_correct=0
shrink_correct=0
base_counts={"H":0,"D":0,"A":0}
new_counts={"H":0,"D":0,"A":0}
actual_counts={"H":0,"D":0,"A":0}

for z in mp:
    r=db[z["match_id"]]
    lh,la=corrected_lambdas(r)
    pb=probs(lh,la)
    lh2,la2=shrink(lh,la,best_s)
    ps=probs(lh2,la2)
    y=outcome(r)
    b=max(pb,key=pb.get)
    q=max(ps,key=ps.get)
    base_correct += b==y
    shrink_correct += q==y
    base_counts[b]+=1
    new_counts[q]+=1
    actual_counts[y]+=1
    rows.append({
        "sample_row":z["sample_row"],"match_id":r[0],"kickoff":r[1],
        "home":r[2],"away":r[3],"actual":y,
        "baseline_pred":b,"shrink_pred":q,
        "lambda_home_before_shrink":lh,"lambda_away_before_shrink":la,
        "lambda_home_after_shrink":lh2,"lambda_away_after_shrink":la2,
        "abs_lambda_gap_before":abs(lh-la),"abs_lambda_gap_after":abs(lh2-la2),
        "baseline_pH":pb["H"],"baseline_pD":pb["D"],"baseline_pA":pb["A"],
        "shrink_pH":ps["H"],"shrink_pD":ps["D"],"shrink_pA":ps["A"]
    })

summary={
    "experiment":"V4 single-module lambda-gap shrink on frozen 500",
    "production_status":"EXPERIMENT_ONLY",
    "baseline_definition":"Existing 51.2% baseline: HIST_WINDOW=15 + pre-cutoff away-lambda bias correction; Dixon-Coles unchanged",
    "n":500,"cutoff":CUTOFF,"hist_window":HIST_WINDOW,
    "train_matches_before_cutoff":len(train),
    "calibration_matches":cal_rows,
    "away_lambda_bias_fixed":away_bias,
    "shrink_parameter_grid":grid,
    "selected_shrink_factor":best_s,
    "calibration_logloss":best_ll,
    "baseline_correct":base_correct,"baseline_accuracy":base_correct/500,
    "baseline_pred_counts":base_counts,
    "shrink_correct":shrink_correct,"shrink_accuracy":shrink_correct/500,
    "shrink_pred_counts":new_counts,
    "actual_counts":actual_counts,
    "improvement_correct":shrink_correct-base_correct,
    "improvement_accuracy":(shrink_correct-base_correct)/500
}
with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
with open(SUM,"w",encoding="utf-8") as f:
    json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False,indent=2))
