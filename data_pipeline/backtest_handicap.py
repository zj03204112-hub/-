import json, math, sqlite3
from collections import defaultdict
from compare_models import run_model

DB="football_model_database.sqlite"
OUT="data/handicap_backtest.json"

def pois(lam,k):
    return math.exp(-lam)*lam**k/math.factorial(k)

RHO=-0.05

def tau(x,y,lh,la):
    if x==0 and y==0: return 1-lh*la*RHO
    if x==0 and y==1: return 1+lh*RHO
    if x==1 and y==0: return 1+la*RHO
    if x==1 and y==1: return 1-RHO
    return 1.0

def probs_for_line(lh,la,line):
    vals={"H":0.0,"D":0.0,"A":0.0}
    for i in range(8):
        for j in range(8):
            p=pois(lh,i)*pois(la,j)*tau(i,j,lh,la)
            d=i+line-j
            if d>0: vals["H"]+=p
            elif abs(d)<1e-9: vals["D"]+=p
            else: vals["A"]+=p
    total=sum(vals.values())
    return {k:v/total for k,v in vals.items()} if total else vals

def actual_for_line(fh,fa,line):
    d=fh+line-fa
    return "H" if d>0 else "D" if abs(d)<1e-9 else "A"

def confidence_bin(conf):
    if conf < 0.40: return "0.33-0.40"
    if conf < 0.50: return "0.40-0.50"
    if conf < 0.60: return "0.50-0.60"
    if conf < 0.70: return "0.60-0.70"
    if conf < 0.80: return "0.70-0.80"
    if conf < 0.90: return "0.80-0.90"
    return "0.90-1.00"

def add_metric(d,pred,actual,p,confidence):
    y={k:0 for k in p}; y[actual]=1
    d["n"]+=1
    d["correct"]+=int(pred==actual)
    d["brier"]+=sum((p[k]-y[k])**2 for k in p)
    d["logloss"]-=math.log(max(1e-12,p[actual]))
    d["confidence_sum"]+=confidence
    d["hit_conf_sum"]+=confidence if pred==actual else 0.0

def finish(d):
    n=d["n"]
    if not n: return {"n":0,"accuracy":None,"brier":None,"logloss":None,"mean_confidence":None}
    return {
        "n":n,
        "accuracy":round(d["correct"]/n,4),
        "brier":round(d["brier"]/n,4),
        "logloss":round(d["logloss"]/n,4),
        "mean_confidence":round(d["confidence_sum"]/n,4),
        "confidence_hit_gap":round(d["confidence_sum"]/n-d["correct"]/n,4)
    }

def empty():
    return {"n":0,"correct":0,"brier":0.0,"logloss":0.0,"confidence_sum":0.0,"hit_conf_sum":0.0}

def main():
    con=sqlite3.connect(DB)
    rows=con.execute("""
      SELECT sm.match_id,sm.handicap,sm.pool_code,sm.source_status,
             r.ft_home,r.ft_away,c.competition_code
      FROM sporttery_market sm
      JOIN results r ON r.match_id=sm.match_id
      JOIN matches m ON m.match_id=sm.match_id
      JOIN competitions c ON c.competition_id=m.competition_id
      WHERE sm.handicap IS NOT NULL
        AND sm.pool_code IN ('asian_handicap_avg','hhad')
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
        AND m.status='finished'
      ORDER BY m.kickoff,sm.match_id
    """).fetchall()

    matches={r[0]:r for r in con.execute("""
      SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
    """).fetchall()}

    out_models={}
    for model in ("v3","v4"):
        overall=empty()
        by_source_line=defaultdict(empty)
        by_league_line=defaultdict(empty)
        by_conf=defaultdict(empty)
        usable=0
        seen=set()

        for mid,line,pool,source,fh,fa,league in rows:
            line=float(line)
            if abs(line-round(line))>1e-9:
                continue
            # Multiple market sources can expose the same match/line. Keep one row
            # per match/pool/line so duplicated provenance cannot overweight a game.
            dedupe=(mid,pool,line)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            match=matches.get(mid)
            if not match:
                continue
            try:
                p_1x2,_=run_model(con,model,match)
                # Reconstruct score-rate inputs from the model's score matrix is
                # unnecessary: run_model's probabilities are 1X2 only. For handicap
                # settlement we use the same T-12h lambdas by recovering them from
                # the model's feature path below.
                from compare_models import model_lambdas
                lh,la=model_lambdas(con,model,match)
            except Exception:
                continue
            p=probs_for_line(lh,la,line)
            actual=actual_for_line(fh,fa,line)
            pred=max(p,key=p.get)
            confidence=p[pred]
            add_metric(overall,pred,actual,p,confidence)
            add_metric(by_source_line[f"{pool}|{line:g}"],pred,actual,p,confidence)
            add_metric(by_league_line[f"{league}|{pool}|{line:g}"],pred,actual,p,confidence)
            add_metric(by_conf[confidence_bin(confidence)],pred,actual,p,confidence)
            usable+=1

        out_models[model]={
            "eligible_rows":len(rows),
            "usable_integer_handicap_rows":usable,
            "overall":finish(overall),
            "confidence_calibration":{k:finish(v) for k,v in sorted(by_conf.items())},
            "by_source_and_line":{k:finish(v) for k,v in sorted(by_source_line.items())},
            "by_league_and_line":{k:finish(v) for k,v in sorted(by_league_line.items())}
        }

    payload={
      "models":out_models,
      "definition":"Integer Asian handicap 3-way settlement evaluated against leakage-safe T-12h model probabilities. Handicap lines are evaluation context, not a direct prediction rule.",
      "data_cutoff":"kickoff minus 12 hours",
      "notes":[
        "V3 is the current production baseline candidate; V4 remains experimental.",
        "The handicap backtest now recomputes each model's T-12h goal rates instead of reading stale prediction_snapshot lambdas.",
        "Model confidence is the maximum predicted H/D/A probability after applying the historical integer handicap to the score distribution; empirical hit rate is reported separately.",
        "Quarter handicaps are excluded from this 3-way integer module and require split-stake Asian settlement.",
        "Football-Data Asian-handicap context is secondary evaluation data; it is not fed directly into the T-12h prediction."
      ]
    }
    with open(OUT,"w",encoding="utf-8") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False))
    con.close()

if __name__=="__main__":
    main()
