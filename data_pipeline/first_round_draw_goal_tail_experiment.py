#!/usr/bin/env python3
"""First-round isolated draw and high-goal-tail experiments.

Uses only completed matches strictly earlier than each fixture's T-12h cutoff.
Chronological split: 60% model development, 20% calibration, 20% untouched test.
Does not modify V5.1 or any historical prediction.
"""
from __future__ import annotations
import json, math, os, sqlite3
from collections import defaultdict, deque
from datetime import datetime, timedelta

DB = os.getenv("FOOTBALL_DB_PATH", "football_model_database.sqlite")
OUT = os.getenv("EXPERIMENT_OUT", "first_round_draw_goal_tail_report.json")
RHO = -0.05
MAX_GOALS = 9
BUCKETS = ("0-1", "2", "3-4", "5-6", "7+")

def bucket(n):
    return "0-1" if n <= 1 else "2" if n == 2 else "3-4" if n <= 4 else "5-6" if n <= 6 else "7+"

def dc_tau(i,j,lh,la):
    if i == 0 and j == 0: return 1 - lh*la*RHO
    if i == 0 and j == 1: return 1 + lh*RHO
    if i == 1 and j == 0: return 1 + la*RHO
    if i == 1 and j == 1: return 1 - RHO
    return 1.0

def poisson_grid(lh,la):
    rows=[]
    for h in range(MAX_GOALS):
        ph=math.exp(-lh)*lh**h/math.factorial(h)
        for a in range(MAX_GOALS):
            pa=math.exp(-la)*la**a/math.factorial(a)
            p=ph*pa*dc_tau(h,a,lh,la)
            rows.append((h,a,max(0.0,p)))
    z=sum(x[2] for x in rows)
    return [(h,a,p/z) for h,a,p in rows]

def normalize_grid(rows, draw_bias=0.0, multipliers=None):
    multipliers = multipliers or {b:1.0 for b in BUCKETS}
    raw=[]
    for h,a,p in rows:
        z=math.exp(draw_bias) if h==a else 1.0
        raw.append((h,a,p*z*multipliers[bucket(h+a)]))
    total=sum(x[2] for x in raw)
    return [(h,a,p/total) for h,a,p in raw]

def probs(rows):
    out={"H":0.0,"D":0.0,"A":0.0}
    goals={b:0.0 for b in BUCKETS}
    for h,a,p in rows:
        out["H" if h>a else "A" if h<a else "D"] += p
        goals[bucket(h+a)] += p
    return out,goals

def metrics(items):
    if not items: return {"n":0}
    n=len(items); brier=ll=0.0; correct=draw_tp=draw_fp=draw_fn=0
    draw_p=[]; goal_ll=0.0; goal_mae=0.0; gd={k:{"n":0,"actual":0} for k in ("0","1","2","3+")}
    for p,actual,gb,actual_gb,eg,ag,net in items:
        y={k:int(k==actual) for k in ("H","D","A")}
        brier += sum((p[k]-y[k])**2 for k in y)
        ll -= math.log(max(1e-15,p[actual]))
        pred=max(p,key=p.get); correct += int(pred==actual)
        draw_tp += int(pred=="D" and actual=="D")
        draw_fp += int(pred=="D" and actual!="D")
        draw_fn += int(pred!="D" and actual=="D")
        draw_p.append(p["D"])
        goal_ll -= math.log(max(1e-15,gb[actual_gb]))
        goal_mae += abs(eg-ag)
        k=str(abs(net)) if abs(net)<3 else "3+"
        gd[k]["n"]+=1; gd[k]["actual"]+=1
    return {
      "n":n,"accuracy":round(correct/n,5),"brier_1x2":round(brier/n,6),
      "logloss_1x2":round(ll/n,6),"draw_recall":round(draw_tp/max(1,draw_tp+draw_fn),5),
      "draw_precision":round(draw_tp/max(1,draw_tp+draw_fp),5),
      "mean_predicted_draw_probability":round(sum(draw_p)/n,5),
      "total_goal_bucket_logloss":round(goal_ll/n,6),
      "expected_total_goals_mae":round(goal_mae/n,5),
      "net_goal_bin_counts":{k:v["n"] for k,v in gd.items()}
    }

def get_matches(con):
    tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"matches","results"}.issubset(tables):
        raise RuntimeError("Required legacy tables matches/results not found; available tables: "+",".join(sorted(tables)[:80]))
    cols={r[1] for r in con.execute("PRAGMA table_info(matches)")}
    rcols={r[1] for r in con.execute("PRAGMA table_info(results)")}
    required={"match_id","kickoff","home_team","away_team","status"}
    if not required.issubset(cols) or not {"match_id","ft_home","ft_away"}.issubset(rcols):
        raise RuntimeError("Unexpected matches/results schema; matches="+str(sorted(cols))+" results="+str(sorted(rcols)))
    q="""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
         FROM matches m JOIN results r ON r.match_id=m.match_id
         WHERE lower(m.status)='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
         ORDER BY m.kickoff,m.match_id"""
    raw=con.execute(q).fetchall()
    valid=[]
    for row in raw:
        try:
            dt=datetime.fromisoformat(str(row[1]).replace("Z","+00:00"))
            if dt.tzinfo: dt=dt.replace(tzinfo=None)
            h,a=int(row[4]),int(row[5])
            if h>=0 and a>=0 and row[2] and row[3]: valid.append((str(row[0]),dt,str(row[2]),str(row[3]),h,a))
        except Exception: pass
    return valid, len(raw), tables

def build_predictions(matches):
    # Team-specific home/away rolling histories; each row enters only after kickoff.
    home_hist=defaultdict(lambda:deque(maxlen=20))
    away_hist=defaultdict(lambda:deque(maxlen=20))
    all_hist=deque(maxlen=1000)
    output=[]
    for m in matches:
        mid,ko,home,away,fh,fa=m
        cutoff=ko-timedelta(hours=12)
        # The sorted stream only contains fixtures with kickoff <= cutoff.
        prior=[x for x in all_hist if x[0] < cutoff]
        if len(prior)<30:
            # Still retain early matches for history, but mark insufficient baseline.
            pass
        league_h=sum(x[3] for x in prior)/max(1,len(prior))
        league_a=sum(x[4] for x in prior)/max(1,len(prior))
        hh=[(gf,ga) for t,gf,ga in home_hist[home] if t < cutoff]
        aa=[(gf,ga) for t,gf,ga in away_hist[away] if t < cutoff]
        # Shrink team splits toward the contemporaneous league mean.
        def avg(vals,idx,base):
            if not vals: return base
            weights=[math.exp(-0.035*(len(vals)-1-i)) for i in range(len(vals))]
            den=sum(weights)
            observed=sum(v[idx]*weights[i] for i,v in enumerate(vals))/den
            n=len(vals)
            return (n*observed+5*base)/(n+5)
        h_att=avg(hh,0,league_h); h_def=avg(hh,1,league_a)
        a_att=avg(aa,0,league_a); a_def=avg(aa,1,league_h)
        # Multiplicative attack/defence ratios around league scoring baselines.
        lh=max(.15,min(4.5,league_h*(h_att/max(.2,league_h))*(a_def/max(.2,league_h))))
        la=max(.15,min(4.5,league_a*(a_att/max(.2,league_a))*(h_def/max(.2,league_a))))
        # Store prediction before adding this match's result to any history.
        if len(prior)>=30 and len(hh)>=2 and len(aa)>=2:
            grid=poisson_grid(lh,la)
            base_p,base_g=probs(grid)
            actual="H" if fh>fa else "A" if fh<fa else "D"
            actual_gb=bucket(fh+fa)
            exp_total=sum((h+a)*p for h,a,p in grid)
            output.append({"id":mid,"kickoff":ko.isoformat(),"grid":grid,"actual":actual,
              "actual_gb":actual_gb,"actual_total":fh+fa,"expected_total":exp_total,
              "net":fh-fa,"base_p":base_p,"base_g":base_g,"lh":lh,"la":la})
        # Add completed score to rolling histories only after its kickoff.
        home_hist[home].append((ko,fh,fa))
        away_hist[away].append((ko,fa,fh))
        all_hist.append((ko,home,away,fh,fa))
    return output

def evaluate(rows,draw_bias=0.0,multipliers=None):
    out=[]
    for r in rows:
        adj=normalize_grid(r["grid"],draw_bias,multipliers)
        p,g=probs(adj)
        out.append((p,g,r["actual_gb"],r["actual_gb"],sum((h+a)*v for h,a,v in adj),r["actual_total"],r["net"]))
    return metrics(out)

def fit_draw_bias(rows):
    best=(float("inf"),0.0)
    for step in range(-30,31):
        b=step/50
        score=evaluate(rows,draw_bias=b)["logloss_1x2"]
        if score<best[0]: best=(score,b)
    return best[1]

def fit_goal_multipliers(rows):
    mult={b:1.0 for b in BUCKETS}
    # Coordinate descent over bucket likelihood multipliers, calibration split only.
    grid=[0.65+i*0.05 for i in range(18)]
    for _ in range(2):
        for b in BUCKETS:
            best=(float("inf"),mult[b])
            for value in grid:
                trial=dict(mult); trial[b]=value
                score=evaluate(rows,multipliers=trial)["total_goal_bucket_logloss"]
                if score<best[0]: best=(score,value)
            mult[b]=best[1]
    return mult

def main():
    con=sqlite3.connect(f"file:{DB}?mode=ro",uri=True)
    matches,joined_count,tables=get_matches(con)
    rows=build_predictions(matches)
    con.close()
    n=len(rows)
    if n<100: raise RuntimeError(f"Only {n} eligible chronological predictions; refusing to fit unstable calibrators.")
    a=int(n*.60); b=int(n*.80)
    dev,cal,test=rows[:a],rows[a:b],rows[b:]
    draw_bias=fit_draw_bias(cal)
    multipliers=fit_goal_multipliers(cal)
    combined=evaluate(test,draw_bias,multipliers)
    report={
      "experiment":"V5.2-first-round-draw-and-goal-tail",
      "status":"COMPLETED_SHADOW_EXPERIMENT",
      "production_v5_1_changed":False,
      "database":os.path.basename(DB),
      "source_tables":{"matches_results_joined_rows":joined_count,"tables_present":sorted(tables)},
      "sample":{"finished_joined":len(matches),"eligible_t12_predictions":n,
        "development_n":len(dev),"calibration_n":len(cal),"untouched_test_n":len(test),
        "time_start":rows[0]["kickoff"],"time_end":rows[-1]["kickoff"],
        "split":"chronological 60% development / 20% calibration / 20% untouched test"},
      "baseline_test":evaluate(test),
      "draw_only_test":evaluate(test,draw_bias=draw_bias),
      "goal_tail_only_test":evaluate(test,multipliers=multipliers),
      "combined_shadow_test":combined,
      "fitted_on_calibration_only":{"draw_logit_bias":draw_bias,"total_goal_bucket_multipliers":multipliers},
      "acceptance_gate":{
        "brier_not_worse":combined["brier_1x2"]<=evaluate(test)["brier_1x2"],
        "logloss_not_worse":combined["logloss_1x2"]<=evaluate(test)["logloss_1x2"],
        "draw_recall_improved_or_equal":combined["draw_recall"]>=evaluate(test)["draw_recall"],
        "total_goal_bucket_logloss_improved":combined["total_goal_bucket_logloss"]<evaluate(test)["total_goal_bucket_logloss"],
        "promotion":"NOT_AUTOMATIC; requires review of all metrics and adequate sample size"
      },
      "limitations":["This first pass uses legacy matches/results only; it does not assume dsl_* detail rows are interchangeable with the legacy match IDs.",
        "No bookmaker O/U or 1X2 snapshots are used in this run.",
        "Goal-tail calibration is based on total-goal buckets; net-goal bins are reported for distribution coverage, not separately calibrated in this first pass."]
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
