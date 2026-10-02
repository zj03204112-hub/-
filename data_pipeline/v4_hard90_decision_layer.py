import csv, math, sqlite3, json, sys
from datetime import datetime, timedelta
sys.path.insert(0, "data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_hard90_decision_500.csv"
TRAIN_END="2026-08-22T00:00:00"

CLASSES=("H","D","A")

def actual(fh,fa):
    return "H" if fh>fa else "D" if fh==fa else "A"

def softmax(z):
    m=max(z); e=[math.exp(max(-50,min(50,x-m))) for x in z]; s=sum(e)
    return [x/s for x in e]

def features(con, match):
    mid, kickoff, home, away, fh, fa = match
    ko=datetime.fromisoformat(kickoff[:19])
    cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
    p,_=cm.run_model(con,"v4",match)
    lh,la=cm.model_lambdas(con,"v4",match)
    rs_h=cm.real_strength_metrics(con,home,cutoff)
    rs_a=cm.real_strength_metrics(con,away,cutoff)
    gap=rs_h["score"]-rs_a["score"]
    return [
        math.log(max(1e-8,p["H"])), math.log(max(1e-8,p["D"])), math.log(max(1e-8,p["A"])),
        p["D"],
        lh, la, lh+la, abs(lh-la),
        gap, abs(gap),
        rs_h["gd"], rs_a["gd"],
        rs_h["stability"], rs_a["stability"],
        rs_h["adj_points"], rs_a["adj_points"],
    ]

def train_softmax(X,Y,reg,epochs=900,lr=0.035):
    # 16 features + intercept; 3-class softmax, L2 regularized.
    d=len(X[0])+1; W=[[0.0]*d for _ in CLASSES]
    for epoch in range(epochs):
        grad=[[0.0]*d for _ in CLASSES]
        for x,y in zip(X,Y):
            xx=[1.0]+x
            z=[sum(W[k][j]*xx[j] for j in range(d)) for k in range(3)]
            q=softmax(z)
            yi=CLASSES.index(y)
            for k in range(3):
                e=q[k]-(1.0 if k==yi else 0.0)
                for j in range(d): grad[k][j]+=e*xx[j]
        n=len(X)
        for k in range(3):
            for j in range(1,d):
                grad[k][j]/=n
                grad[k][j]+=reg*W[k][j]
            grad[k][0]/=n
        # conservative decay / step
        for k in range(3):
            for j in range(d): W[k][j]-=lr*grad[k][j]
    return W

def predict(W,x):
    xx=[1.0]+x
    return dict(zip(CLASSES,softmax([sum(W[k][j]*xx[j] for j in range(len(xx))) for k in range(3)])))

def accuracy(W,X,Y):
    return sum(max(predict(W,x),key=predict(W,x).get)==y for x,y in zip(X,Y))/len(Y)

def metrics(W,X,Y):
    n=len(Y); correct=0; brier=0; ll=0; pd=0; dc=0; ad=0
    for x,y in zip(X,Y):
        p=predict(W,x); pred=max(p,key=p.get)
        correct+=pred==y; pd+=pred=="D"; ad+=y=="D"; dc+=pred=="D" and y=="D"
        brier+=sum((p[k]-(1 if k==y else 0))**2 for k in CLASSES)
        ll-=math.log(max(1e-12,p[y]))
    return {"n":n,"accuracy":round(correct/n,4),"correct":correct,"brier":round(brier/n,5),
            "logloss":round(ll/n,5),"pred_draws":pd,"actual_draws":ad,
            "draw_correct":dc,"draw_recall":round(dc/ad,4) if ad else 0}

con=sqlite3.connect(DB)
rows=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
ORDER BY m.kickoff,m.match_id""").fetchall()

with open(MAP,encoding="utf-8-sig",newline="") as f: mp=list(csv.DictReader(f))
if len(mp)!=500 or len({r["match_id"] for r in mp})!=500: raise RuntimeError("FROZEN_500_INVALID")
byid={r[0]:r for r in rows}
frozen=[byid[m["match_id"]] for m in mp]
train=[x for x in rows if x[1][:19] < TRAIN_END]
if len(train)<2000: raise RuntimeError(f"TRAIN_TOO_SMALL {len(train)}")

# Build meta-features only from information available at T-12h.
TX=[features(con,x) for x in train]; TY=[actual(x[4],x[5]) for x in train]
split=max(1000,int(len(train)*0.75))
Xtr,Ytr=TX[:split],TY[:split]
Xva,Yva=TX[split:],TY[split:]

# Select regularization only on an earlier chronological validation slice.
best=None
for reg in (0.0005,0.001,0.002,0.005,0.01,0.02,0.05,0.1):
    W=train_softmax(Xtr,Ytr,reg)
    m=metrics(W,Xva,Yva)
    key=(-m["accuracy"],m["logloss"],m["brier"])
    if best is None or key<best[0]: best=(key,reg,m)

# Refit on ALL pre-frozen history with the selected regularization.
W=train_softmax(TX,TY,best[1],epochs=1100,lr=0.03)
FX=[features(con,x) for x in frozen]; FY=[actual(x[4],x[5]) for x in frozen]
m=metrics(W,FX,FY)

# Baseline V4 on exactly the same 500.
base_items=[]
for x in frozen:
    p,a=cm.run_model(con,"v4",x); base_items.append((p,a))
base_correct=sum(max(p,key=p.get)==a for p,a in base_items)

out=[]
for x,fx,a,(bp,ba) in zip(frozen,FX,FY,base_items):
    q=predict(W,fx)
    out.append({"match_id":x[0],"kickoff":x[1],"home":x[2],"away":x[3],
                "actual":a,"base_pred":max(bp,key=bp.get),
                "base_H":round(bp["H"],6),"base_D":round(bp["D"],6),"base_A":round(bp["A"],6),
                "decision_pred":max(q,key=q.get),
                "decision_H":round(q["H"],6),"decision_D":round(q["D"],6),"decision_A":round(q["A"],6),
                "changed":max(bp,key=bp.get)!=max(q,key=q.get)})

with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(out[0])); w.writeheader(); w.writerows(out)

report={"train_n":len(train),"internal_train_n":len(Xtr),"internal_validation_n":len(Xva),
        "selected_regularization":best[1],"internal_validation":best[2],
        "frozen_v4_baseline":{"n":500,"correct":base_correct,"accuracy":round(base_correct/500,4)},
        "frozen_decision_layer":m,
        "hard90_target":{"required_correct":450,"required_accuracy":0.90},
        "production_status":"EXPERIMENT_ONLY","output":OUT}
print(json.dumps(report,ensure_ascii=False))
