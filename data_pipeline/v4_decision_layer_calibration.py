import csv, math, sqlite3, sys
from datetime import datetime, timedelta
sys.path.insert(0, "data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_decision_layer_calibration_500.csv"
TRAIN_END="2026-08-22T00:00:00"

def actual(fh,fa):
    return "H" if fh>fa else "D" if fh==fa else "A"

def apply(p,t=1.0,b=0.0):
    z={k:math.log(max(1e-12,p[k]))/t for k in ("H","D","A")}
    z["D"]+=b
    mx=max(z.values()); ex={k:math.exp(z[k]-mx) for k in z}; s=sum(ex.values())
    return {k:ex[k]/s for k in ex}

def metrics(items):
    n=len(items); correct=sum(max(p,key=p.get)==a for p,a in items)
    brier=sum(sum((p[k]-(1 if k==a else 0))**2 for k in p) for p,a in items)/n
    ll=-sum(math.log(max(1e-12,p[a])) for p,a in items)/n
    draws=sum(max(p,key=p.get)=="D" for p,a in items)
    actual_draws=sum(a=="D" for p,a in items)
    draw_correct=sum(max(p,key=p.get)=="D" and a=="D" for p,a in items)
    return {"n":n,"accuracy":round(correct/n,4),"brier":round(brier,5),"logloss":round(ll,5),
            "pred_draws":draws,"actual_draws":actual_draws,"draw_correct":draw_correct,
            "draw_recall":round(draw_correct/actual_draws,4) if actual_draws else 0}

con=sqlite3.connect(DB)
rows=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
ORDER BY m.kickoff,m.match_id""").fetchall()

# Frozen IDs and strict integrity
with open(MAP,encoding="utf-8-sig",newline="") as f: mp=list(csv.DictReader(f))
if len(mp)!=500 or len({r["match_id"] for r in mp})!=500: raise RuntimeError("FROZEN_500_INVALID")
byid={r[0]:r for r in rows}
frozen=[]
for m in mp:
    x=byid[m["match_id"]]
    frozen.append(x)

# Training pool ends before the frozen sample starts: no frozen result can influence calibration.
train=[x for x in rows if x[1][:19] < TRAIN_END]
if len(train)<1000: raise RuntimeError(f"TRAIN_TOO_SMALL {len(train)}")

train_items=[]
for x in train:
    p,_=cm.run_model(con,"v4",x)
    train_items.append((p,actual(x[4],x[5])))

# Fit temperature + draw bias jointly on chronological pre-frozen data.
best=None
for ti in range(70,131):
    t=ti/100
    for bi in range(-20,31):
        b=bi/100
        mm=metrics([(apply(p,t,b),a) for p,a in train_items])
        key=(mm["logloss"],mm["brier"])
        if best is None or key<best[0]:
            best=(key,t,b,mm)

t,b=best[1],best[2]
base=[]; cal=[]
for x in frozen:
    p,a=cm.run_model(con,"v4",x)
    base.append((p,a))
    cal.append((apply(p,t,b),a))

base_m=metrics(base); cal_m=metrics(cal)

out=[]
for x,(p,a),(q,_) in zip(frozen,base,cal):
    out.append({
      "match_id":x[0],"kickoff":x[1],"home":x[2],"away":x[3],
      "actual":a,
      "base_pred":max(p,key=p.get),
      "base_H":round(p["H"],6),"base_D":round(p["D"],6),"base_A":round(p["A"],6),
      "cal_pred":max(q,key=q.get),
      "cal_H":round(q["H"],6),"cal_D":round(q["D"],6),"cal_A":round(q["A"],6),
      "changed":max(p,key=p.get)!=max(q,key=q.get)
    })

with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(out[0])); w.writeheader(); w.writerows(out)

print({"train_n":len(train),"fit_temperature":t,"fit_draw_bias":b,
       "train_fit_metrics":best[3],"frozen_base":base_m,"frozen_calibrated":cal_m,
       "output":OUT})
