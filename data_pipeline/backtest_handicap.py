import json, math, sqlite3
from collections import defaultdict

DB="football_model_database.sqlite"
OUT="data/handicap_backtest.json"

def pois(lam,k):
    return math.exp(-lam)*lam**k/math.factorial(k)

def probs_for_line(lh,la,line):
    vals={"H":0.0,"D":0.0,"A":0.0}
    for i in range(7):
        for j in range(7):
            p=pois(lh,i)*pois(la,j)
            d=i+line-j
            if d>0: vals["H"]+=p
            elif abs(d)<1e-9: vals["D"]+=p
            else: vals["A"]+=p
    return vals

def actual_for_line(fh,fa,line):
    d=fh+line-fa
    return "H" if d>0 else "D" if abs(d)<1e-9 else "A"

def main():
    con=sqlite3.connect(DB)
    rows=con.execute("""
      SELECT sm.match_id,sm.handicap,sm.pool_code,sm.source_status,
             r.ft_home,r.ft_away,ps.notes
      FROM sporttery_market sm
      JOIN results r ON r.match_id=sm.match_id
      JOIN prediction_snapshot ps ON ps.match_id=sm.match_id
      WHERE sm.handicap IS NOT NULL
        AND sm.pool_code IN ('asian_handicap_avg','hhad')
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
        AND ps.notes LIKE '%"model":"baseline_poisson_t12_v1"%'
    """).fetchall()

    agg=defaultdict(lambda: {"n":0,"correct":0,"brier":0.0,"logloss":0.0})
    overall=defaultdict(lambda: {"n":0,"correct":0,"brier":0.0,"logloss":0.0})
    usable=0

    for mid,line,pool,source,fh,fa,notes in rows:
        if abs(float(line)-round(float(line)))>1e-9:
            continue
        try:
            meta=json.loads(notes)
            lh,la=meta["lambda"]
        except Exception:
            continue
        line=float(line)
        p=probs_for_line(lh,la,line)
        actual=actual_for_line(fh,fa,line)
        pred=max(p,key=p.get)
        y={k:0 for k in p}; y[actual]=1
        b=sum((p[k]-y[k])**2 for k in p)
        ll=-math.log(max(1e-12,p[actual]))
        key=f"{pool}|{line:g}"
        for a,key2 in ((agg,key),(overall,"ALL")):
            a[key2]["n"]+=1
            a[key2]["correct"]+=int(pred==actual)
            a[key2]["brier"]+=b
            a[key2]["logloss"]+=ll
        usable+=1

    def finish(d):
        out={}
        for k,v in d.items():
            n=v["n"]
            out[k]={"n":n,"accuracy":round(v["correct"]/n,4),
                    "brier":round(v["brier"]/n,4),
                    "logloss":round(v["logloss"]/n,4)}
        return out

    payload={
      "model":"baseline_poisson_t12_v1",
      "definition":"Integer Asian handicap 3-way settlement; handicap used only to stratify/evaluate the T-12h model, not as a direct prediction rule.",
      "data_cutoff":"kickoff minus 12 hours",
      "eligible_rows":len(rows),
      "usable_integer_handicap_rows":usable,
      "overall":finish(overall),
      "by_source_and_line":finish(agg)
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
