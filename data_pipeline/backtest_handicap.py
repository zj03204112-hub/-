import json, math, sqlite3
from collections import defaultdict
from compare_models import model_lambdas, fit_temperature, apply_temperature

DB="football_model_database.sqlite"
OUT="data/handicap_backtest.json"
RHO=-0.05
PRIORITY={"hhad":5,"asian_handicap_avg":4,"sofascore_asian_featured":3,
          "football_data_ah_close":2,"football_data_ah_bookmaker":1,"football_data_ah_open":1}

def pois(lam,k): return math.exp(-lam)*lam**k/math.factorial(k)
def tau(x,y,lh,la):
    if x==0 and y==0: return 1-lh*la*RHO
    if x==0 and y==1: return 1+lh*RHO
    if x==1 and y==0: return 1+la*RHO
    if x==1 and y==1: return 1-RHO
    return 1.0

def canonical_line(x):
    try:
        v=float(x)
        return int(round(v)) if abs(v-round(v))<1e-9 else None
    except Exception: return None

def probs_for_line(lh,la,line):
    vals={"H":0.0,"D":0.0,"A":0.0}
    for i in range(10):
        for j in range(10):
            p=pois(lh,i)*pois(la,j)*tau(i,j,lh,la)
            d=i+line-j
            vals["H" if d>0 else "D" if d==0 else "A"]+=p
    s=sum(vals.values())
    return {k:v/s for k,v in vals.items()} if s else vals

def actual_for_line(fh,fa,line):
    d=fh+line-fa
    return "H" if d>0 else "D" if d==0 else "A"

def top_scores(lh,la,n=2):
    scores=[]
    for i in range(10):
        for j in range(10):
            p=pois(lh,i)*pois(la,j)*tau(i,j,lh,la)
            scores.append((p,i,j))
    total=sum(x[0] for x in scores)
    return sorted([(p/total,i,j) for p,i,j in scores],reverse=True)[:n]

def score_result(i,j,line):
    d=i+line-j
    return "H" if d>0 else "D" if d==0 else "A"

def confidence_bin(c):
    return "0.33-0.40" if c<.40 else "0.40-0.50" if c<.50 else "0.50-0.60" if c<.60 else "0.60-0.70" if c<.70 else "0.70-0.80" if c<.80 else "0.80-0.90" if c<.90 else "0.90-1.00"

def metric(): return {"n":0,"correct":0,"brier":0.0,"logloss":0.0,"confidence_sum":0.0}
def add(d,pred,actual,p):
    y={k:0 for k in p}; y[actual]=1
    d["n"]+=1; d["correct"]+=int(pred==actual)
    d["brier"]+=sum((p[k]-y[k])**2 for k in p)
    d["logloss"]-=math.log(max(1e-12,p[actual])); d["confidence_sum"]+=max(p.values())
def finish(d):
    n=d["n"]
    return {"n":n,"accuracy":round(d["correct"]/n,4) if n else None,
            "brier":round(d["brier"]/n,4) if n else None,
            "logloss":round(d["logloss"]/n,4) if n else None,
            "mean_confidence":round(d["confidence_sum"]/n,4) if n else None,
            "confidence_hit_gap":round(d["confidence_sum"]/n-d["correct"]/n,4) if n else None}

def main():
    con=sqlite3.connect(DB)
    raw=con.execute("""
      SELECT sm.match_id,sm.handicap,sm.pool_code,r.ft_home,r.ft_away,
             c.competition_code,m.kickoff
      FROM sporttery_market sm
      JOIN results r ON r.match_id=sm.match_id
      JOIN matches m ON m.match_id=sm.match_id
      JOIN competitions c ON c.competition_id=m.competition_id
      WHERE sm.handicap IS NOT NULL
        AND sm.pool_code IN ('asian_handicap_avg','hhad','sofascore_asian_featured','football_data_ah_close','football_data_ah_bookmaker','football_data_ah_open')
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL AND m.status='finished'
      ORDER BY m.kickoff,sm.match_id
    """).fetchall()

    chosen={}
    for mid,line,pool,fh,fa,league,ko in raw:
        line=canonical_line(line)
        if line is None: continue
        key=(mid,line)
        if key not in chosen or PRIORITY.get(pool,0)>PRIORITY.get(chosen[key][2],0):
            chosen[key]=(mid,line,pool,fh,fa,league,ko)

    out_models={}
    for model in ("v3","v4"):
        samples=[]
        audit={"n":0,"top1_consistent":0,"top2_consistent":0,"both_consistent":0,
               "inconsistent_examples":[]}
        for mid,line,pool,fh,fa,league,ko in chosen.values():
            match=con.execute("""
              SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
              FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.match_id=?
            """,(mid,)).fetchone()
            if not match: continue
            try:
                lh,la=model_lambdas(con,model,match)
                p=probs_for_line(lh,la,line)
                scores=top_scores(lh,la,2)
            except Exception: continue
            pred=max(p,key=p.get)
            r1=score_result(scores[0][1],scores[0][2],line)
            r2=score_result(scores[1][1],scores[1][2],line)
            audit["n"]+=1
            audit["top1_consistent"]+=int(r1==pred)
            audit["top2_consistent"]+=int(r2==pred)
            audit["both_consistent"]+=int(r1==pred and r2==pred)
            if (r1!=pred or r2!=pred) and len(audit["inconsistent_examples"])<10:
                audit["inconsistent_examples"].append({
                    "match_id":mid,"line":line,"pred":pred,
                    "score1":f"{scores[0][1]}-{scores[0][2]}","score1_result":r1,
                    "score2":f"{scores[1][1]}-{scores[1][2]}","score2_result":r2})
            samples.append({"mid":mid,"line":line,"pool":pool,"league":league,
                            "kickoff":ko,"p":p,"actual":actual_for_line(fh,fa,line),
                            "top_scores":[{"score":f"{i}-{j}","probability":round(pr,6),
                                           "handicap_result":score_result(i,j,line)}
                                          for pr,i,j in scores]})

        samples.sort(key=lambda x:(x["kickoff"],x["mid"]))
        split=max(1,int(len(samples)*.60))
        train=[(s["p"],s["actual"]) for s in samples[:split]]
        global_t=fit_temperature(train) if train else 1.0
        line_train=defaultdict(list)
        for s in samples[:split]: line_train[s["line"]].append((s["p"],s["actual"]))
        line_t={line:(fit_temperature(items) if len(items)>=20 else global_t) for line,items in line_train.items()}

        raw_m=metric(); cal_m=metric()
        raw_bins=defaultdict(metric); cal_bins=defaultdict(metric)
        by_line=defaultdict(lambda:{"raw":metric(),"calibrated":metric()})
        by_source=defaultdict(lambda:{"raw":metric(),"calibrated":metric()})
        for s in samples[split:]:
            pr=s["p"]; pc=apply_temperature(pr,line_t.get(s["line"],global_t))
            pred_r=max(pr,key=pr.get); pred_c=max(pc,key=pc.get)
            add(raw_m,pred_r,s["actual"],pr); add(cal_m,pred_c,s["actual"],pc)
            add(raw_bins[confidence_bin(max(pr.values()))],pred_r,s["actual"],pr)
            add(cal_bins[confidence_bin(max(pc.values()))],pred_c,s["actual"],pc)
            add(by_line[s["line"]]["raw"],pred_r,s["actual"],pr)
            add(by_line[s["line"]]["calibrated"],pred_c,s["actual"],pc)
            add(by_source[s["pool"]]["raw"],pred_r,s["actual"],pr)
            add(by_source[s["pool"]]["calibrated"],pred_c,s["actual"],pc)

        out_models[model]={
            "eligible_market_rows":len(raw),"deduped_match_line_rows":len(chosen),
            "test_rows":len(samples)-split,"raw":finish(raw_m),"calibrated":finish(cal_m),
            "temperature_global":global_t,
            "temperature_by_integer_line":{str(k):v for k,v in sorted(line_t.items())},
            "confidence_calibration_raw":{k:finish(v) for k,v in sorted(raw_bins.items())},
            "confidence_calibration_calibrated":{k:finish(v) for k,v in sorted(cal_bins.items())},
            "by_line":{str(k):{"raw":finish(v["raw"]),"calibrated":finish(v["calibrated"])} for k,v in sorted(by_line.items())},
            "by_source":{k:{"raw":finish(v["raw"]),"calibrated":finish(v["calibrated"])} for k,v in sorted(by_source.items())},
            "score_mapping_audit":audit
        }

    payload={"models":out_models,
      "definition":"Leakage-safe T-12h integer Asian handicap evaluation with canonical home-perspective line mapping and chronological probability calibration.",
      "mapping":{"home_minus_1":"line=-1","home_0":"line=0","home_plus_1":"line=+1",
                 "settlement":"H if home_goals+line>away_goals; D if equal; A if lower",
                 "quarter_and_half_lines":"excluded from this 3-way integer module"},
      "market_priority":["hhad","asian_handicap_avg","sofascore_asian_featured"],
      "calibration":"Temperature fitted on first 60% chronologically; evaluated on later 40%. Line-specific calibration requires >=20 chronological training samples, otherwise global temperature is used. Minimum is intentionally lower only for integer handicap strata; thin strata remain separately reported.",
      "notes":["V3 remains production baseline candidate; V4 remains experimental.",
               "Handicap is evaluation context, not a direct prediction feature.",
               "Model confidence and empirical hit rate are reported separately."]}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
