import json, math, sqlite3
from collections import defaultdict

DB="football_model_database.sqlite"
OUT="data/handicap_backtest.json"

def pois(lam,k):
    return math.exp(-lam)*lam**k/math.factorial(k)

def probs_for_line(lh,la,line):
    vals={"H":0.0,"D":0.0,"A":0.0}
    for i in range(8):
        for j in range(8):
            p=pois(lh,i)*pois(la,j)
            d=i+line-j
            if d>0: vals["H"]+=p
            elif abs(d)<1e-9: vals["D"]+=p
            else: vals["A"]+=p
    total=sum(vals.values())
    if total:
        vals={k:v/total for k,v in vals.items()}
    return vals

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

def add_metric(d, key, pred, actual, p):
    y={k:0 for k in p}
    y[actual]=1
    b=sum((p[k]-y[k])**2 for k in p)
    ll=-math.log(max(1e-12,p[actual]))
    d[key]["n"]+=1
    d[key]["correct"]+=int(pred==actual)
    d[key]["brier"]+=b
    d[key]["logloss"]+=ll

def finish(d):
    out={}
    for k,v in d.items():
        n=v["n"]
        out[k]={
            "n":n,
            "accuracy":round(v["correct"]/n,4),
            "brier":round(v["brier"]/n,4),
            "logloss":round(v["logloss"]/n,4)
        }
    return out

def main():
    con=sqlite3.connect(DB)
    rows=con.execute("""
      SELECT sm.match_id,sm.handicap,sm.pool_code,sm.source_status,
             r.ft_home,r.ft_away,ps.notes,c.competition_code
      FROM sporttery_market sm
      JOIN results r ON r.match_id=sm.match_id
      JOIN prediction_snapshot ps ON ps.match_id=sm.match_id
      JOIN matches m ON m.match_id=sm.match_id
      JOIN competitions c ON c.competition_id=m.competition_id
      WHERE sm.handicap IS NOT NULL
        AND sm.pool_code IN ('asian_handicap_avg','hhad')
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
        AND ps.notes LIKE '%"model":"dc_poisson_t12_v2"%'
    """).fetchall()

    by_source_line=defaultdict(lambda: {"n":0,"correct":0,"brier":0.0,"logloss":0.0})
    by_league_line=defaultdict(lambda: {"n":0,"correct":0,"brier":0.0,"logloss":0.0})
    by_conf=defaultdict(lambda: {"n":0,"correct":0,"brier":0.0,"logloss":0.0})
    overall=defaultdict(lambda: {"n":0,"correct":0,"brier":0.0,"logloss":0.0})
    usable=0

    for mid,line,pool,source,fh,fa,notes,league in rows:
        line=float(line)
        if abs(line-round(line))>1e-9:
            continue
        try:
            meta=json.loads(notes)
            lh,la=meta["lambda"]
        except Exception:
            continue

        p=probs_for_line(lh,la,line)
        actual=actual_for_line(fh,fa,line)
        pred=max(p,key=p.get)
        confidence=p[pred]

        add_metric(by_source_line, f"{pool}|{line:g}", pred, actual, p)
        add_metric(by_league_line, f"{league}|{pool}|{line:g}", pred, actual, p)
        add_metric(by_conf, confidence_bin(confidence), pred, actual, p)
        add_metric(overall, "ALL", pred, actual, p)
        usable+=1

    payload={
      "model":"baseline_poisson_t12_v1",
      "definition":"Integer Asian handicap 3-way settlement; handicap is evaluation/stratification context, not a direct prediction rule.",
      "data_cutoff":"kickoff minus 12 hours",
      "eligible_rows":len(rows),
      "usable_integer_handicap_rows":usable,
      "overall":finish(overall),
      "confidence_calibration":finish(by_conf),
      "by_source_and_line":finish(by_source_line),
      "by_league_and_line":finish(by_league_line),
      "notes":[
        "Model confidence is the maximum predicted H/D/A probability after applying the historical integer handicap to the T-12h Poisson score distribution.",
        "Empirical hit rate is measured separately and must not be treated as equal to model confidence.",
        "Quarter handicaps are excluded from this 3-way integer handicap module and will be handled by a separate split-stake Asian settlement module.",
        "Football-Data Asian-handicap context is secondary evaluation data; it is not fed directly into the baseline prediction."
      ]
    }
    with open(OUT,"w",encoding="utf-8") as f:
        json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False))
    con.close()

if __name__=="__main__":
    main()
