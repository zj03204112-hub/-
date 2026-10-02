import csv, json, math, sqlite3, sys
from datetime import datetime, timedelta
sys.path.insert(0, "data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_matrix_draw_structure_500.csv"
SUMMARY="model_validation/v4_matrix_draw_structure_500_summary.json"
EPS=1e-12

def actual(r):
    return "H" if r[4]>r[5] else "D" if r[4]==r[5] else "A"

def sigmoid(z):
    z=max(-35.0,min(35.0,z))
    return 1.0/(1.0+math.exp(-z))

# IMPORTANT: this feature generator NEVER reads cm.run_model() or aggregated P(D).
# It consumes only the 8x8 score-probability matrix.
def structure_features(m):
    diag=[m[i][i] for i in range(cm.N)]
    low=sum(m[i][j] for i in range(3) for j in range(3))
    diag_mass=sum(diag)
    near_diag=sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if abs(i-j)<=1)
    exact_01=diag[0]+diag[1]
    exact_23=diag[2]+diag[3]
    exact_45=diag[4]+diag[5]
    # Relative concentration: how much of the matrix is in equal-score cells
    # compared with the nearby-score band. These are matrix-native structures.
    ratios=[diag[i]/max(m[i][i-1]+m[i][i]+m[i][i+1] if 0<=i<cm.N else EPS,EPS)
            for i in range(cm.N)]
    return [
        1.0,
        math.log(max(diag[0],EPS)), math.log(max(diag[1],EPS)),
        math.log(max(diag[2],EPS)), math.log(max(diag[3],EPS)),
        math.log(max(diag[4],EPS)),
        math.log(max(diag_mass,EPS)),
        low, near_diag, diag_mass/max(near_diag,EPS),
        exact_01, exact_23, exact_45,
        ratios[0], ratios[1], ratios[2], ratios[3]
    ]

def dot(w,x):
    return sum(a*b for a,b in zip(w,x))

def fit_logistic(items, l2):
    d=len(items[0][0]); w=[0.0]*d
    # Fixed-step gradient descent with conservative backtracking.
    for it in range(3500):
        grad=[0.0]*d
        loss=0.0
        for x,y in items:
            p=sigmoid(dot(w,x))
            loss += -(y*math.log(max(p,EPS))+(1-y)*math.log(max(1-p,EPS)))
            e=p-y
            for k in range(d): grad[k]+=e*x[k]
        for k in range(1,d):
            loss += 0.5*l2*w[k]*w[k]
            grad[k] += l2*w[k]
        lr=0.02/(1.0+it/1200.0)
        for k in range(d): w[k]-=lr*grad[k]/len(items)
    return w

def fit_conditional_ha(items):
    # H/A layer is derived only from off-diagonal matrix mass.
    # Learn one calibration temperature on log(H/A mass ratio) using training data.
    best_t,best_loss=1.0,float("inf")
    for q in range(50,201):
        t=q/100.0
        loss=0.0
        for h,a,y in items:
            z=math.log(max(h,EPS)/max(a,EPS))/t
            p=sigmoid(z)
            loss-=math.log(max(p if y=="H" else 1-p,EPS))
        if loss<best_loss: best_loss,best_t=loss,t
    return best_t

def matrix_from_match(con,r):
    lh,la=cm.model_lambdas(con,"v4",r)
    # DC matrix is the existing score-generation source. No aggregated P(D)
    # is used anywhere in the new decision layer.
    return cm.matrix(lh,la,True)

def predict_from_matrix(m,w,t_ha):
    x=structure_features(m)
    p_draw=sigmoid(dot(w,x))
    h_mass=sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j)
    a_mass=sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j)
    z=math.log(max(h_mass,EPS)/max(a_mass,EPS))/t_ha
    qh=sigmoid(z)
    p={"D":p_draw,"H":(1-p_draw)*qh,"A":(1-p_draw)*(1-qh)}
    return p,p_draw,h_mass,a_mass,x

def metrics(items):
    n=len(items); correct=0; brier=0; ll=0
    conf=[]
    for p,a in items:
        pred=max(p,key=p.get); correct+=pred==a
        y={k:0 for k in p}; y[a]=1
        brier+=sum((p[k]-y[k])**2 for k in p)
        ll-=math.log(max(p[a],EPS))
        conf.append((max(p.values()),pred==a))
    return {"n":n,"correct":correct,"accuracy":correct/n if n else 0,
            "brier":brier/n if n else 0,"logloss":ll/n if n else 0,
            "predicted_draws":sum(max(p,key=p.get)=="D" for p,_ in items),
            "actual_draws":sum(a=="D" for _,a in items),
            "draw_correct":sum(max(p,key=p.get)=="D" and a=="D" for p,a in items)}

def auc(pos,neg,key):
    wins=0; n=0
    for a in pos:
        for b in neg:
            x=key(a); y=key(b); n+=1
            wins += 1 if x>y else 0.5 if x==y else 0
    return wins/n if n else 0

con=sqlite3.connect(DB)
with open(MAP,encoding="utf-8-sig") as f: mp=list(csv.DictReader(f))
assert len(mp)==500 and len({x["match_id"] for x in mp})==500 and all(x["mapping_status"]=="unique" for x in mp)
db={r[0]:r for r in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.status='finished' AND r.ft_home IS NOT NULL""")}
cutoff="2026-08-22T00:00:00"
train_rows=[]; test_rows=[]
for r in db.values():
    if r[1] < cutoff:
        train_rows.append(r)
for z in mp:
    test_rows.append(db[z["match_id"]])
assert len(train_rows)>2000 and len(test_rows)==500

# Cache matrices once. All matrices are generated strictly from T-12h V4 source.
train=[]
for r in train_rows:
    m=matrix_from_match(con,r)
    train.append((structure_features(m), 1 if actual(r)=="D" else 0, 
                  sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j),
                  sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j)))
test=[]
for r in test_rows:
    m=matrix_from_match(con,r)
    test.append((r,m))

# Chronological internal validation selects L2 and HA temperature only.
split=max(1,int(len(train)*0.75))
candidates=[0.001,0.003,0.01,0.03,0.1,0.3,1.0]
best=(None,None,-1)
for l2 in candidates:
    w=fit_logistic(train[:split][0:len(train[:split])],l2)
    draw_val=[(sigmoid(dot(w,x)),y) for x,y,_,_ in train[split:]]
    acc=sum((p>=0.5)==bool(y) for p,y in draw_val)/len(draw_val)
    # secondary criterion is logloss
    ll=-sum(math.log(max(p if y else 1-p,EPS)) for p,y in draw_val)/len(draw_val)
    score=acc-0.03*ll
    if score>best[2]: best=(l2,w,score)

l2=best[0]
w=fit_logistic(train,l2)
ha_items=[(h,a,actual(r)) for r,m in train_rows and []]
# Build conditional H/A training set from matrices directly.
ha_train=[]
for r in train_rows:
    m=matrix_from_match(con,r)
    if actual(r)!="D":
        ha_train.append((sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j),
                         sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j),actual(r)))
t_ha=fit_conditional_ha(ha_train)

out=[]
items=[]
for r,m in test:
    p,pd,hm,am,x=predict_from_matrix(m,w,t_ha)
    a=actual(r); items.append((p,a))
    out.append({"sample_row":next(z["sample_row"] for z in mp if z["match_id"]==r[0]),
                "match_id":r[0],"kickoff":r[1],"home":r[2],"away":r[3],
                "actual":a,"home_score":r[4],"away_score":r[5],
                "new_draw_structure_probability":pd,
                "new_H":p["H"],"new_D":p["D"],"new_A":p["A"],
                "prediction":max(p,key=p.get),
                "raw_matrix_H_mass":hm,"raw_matrix_A_mass":am,
                "diag_mass":sum(m[i][i] for i in range(cm.N)),
                "p00":m[0][0],"p11":m[1][1],"p22":m[2][2],"p33":m[3][3],
                "low_3x3":sum(m[i][j] for i in range(3) for j in range(3)),
                "matrix_top_score":max(((m[i][j],f"{i}-{j}") for i in range(cm.N) for j in range(cm.N)),key=lambda z:z[0])[1]})

base=[]
for r,m in test:
    p= {"H":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i>j),
        "D":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i==j),
        "A":sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i<j)}
    base.append((p,actual(r)))

res=metrics(items); bres=metrics(base)
draw_struct=[(r["new_draw_structure_probability"],r["actual"]) for r in out]
pos=[r for r in out if r["actual"]=="D"]; neg=[r for r in out if r["actual"]!="D"]
summary={"experiment":"8x8 matrix -> independent draw structure probability -> H/D/A",
 "production_status":"EXPERIMENT_ONLY","n":500,"actual_draws":116,
 "train_matches_before_cutoff":len(train_rows),"cutoff":cutoff,
 "features":"8x8 matrix only; no original aggregated P(D), no P(D) threshold",
 "selected_l2":l2,"ha_temperature":t_ha,
 "new":res,"baseline_matrix_argmax":bres,
 "improvement_correct":res["correct"]-bres["correct"],
 "improvement_accuracy":res["accuracy"]-bres["accuracy"],
 "draw_structure_auc":auc(pos,neg,lambda r:r["new_draw_structure_probability"]),
 "draw_structure_mean_draw":sum(r["new_draw_structure_probability"] for r in pos)/len(pos),
 "draw_structure_mean_non_draw":sum(r["new_draw_structure_probability"] for r in neg)/len(neg),
 "confusion_new":{a:{b:sum(r["actual"]==a and r["prediction"]==b for r in out) for b in "HDA"} for a in "HDA"}}
with open(OUT,"w",encoding="utf-8",newline="") as f:
    wri=csv.DictWriter(f,fieldnames=list(out[0].keys())); wri.writeheader(); wri.writerows(out)
with open(SUMMARY,"w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False))
