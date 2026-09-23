import json, math, sqlite3, os
from collections import defaultdict, Counter
from compare_models import model_lambdas, fit_temperature, apply_temperature

DB="football_model_database.sqlite"
BASIS=os.environ.get("HANDICAP_SAMPLE_BASIS","asian").strip().lower()
OUT="data/handicap_backtest_sporttery.json" if BASIS=="sporttery" else "data/handicap_backtest.json"
RHO=-0.05
ASIAN_PRIORITY={"asian_handicap_avg":6,"sofascore_asian_featured":5,"sgodds_open":4,"football_data_ah_close":3,"football_data_ah_bookmaker":2,"football_data_ah_open":1}
SPORTTERY_PRIORITY={"hhad":10}
PRIORITY=SPORTTERY_PRIORITY if BASIS=="sporttery" else ASIAN_PRIORITY
SAMPLE_SOURCES=tuple(SPORTTERY_PRIORITY) if BASIS=="sporttery" else tuple(ASIAN_PRIORITY)
TARGET_LINES={-3,-2,-1,1,2,3}
PRIOR_WEIGHT=0.50
PRIOR_ALPHA=3.0

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

def score_result(i,j,line):
    d=i+line-j
    return "H" if d>0 else "D" if d==0 else "A"

def top_scores(lh,la,n=2):
    scores=[]
    for i in range(10):
        for j in range(10):
            p=pois(lh,i)*pois(la,j)*tau(i,j,lh,la)
            scores.append((p,i,j))
    total=sum(x[0] for x in scores)
    return sorted([(p/total,i,j) for p,i,j in scores],reverse=True)[:n]

def top_scores_for_result(lh,la,line,pred,n=2):
    scores=[]
    for i in range(10):
        for j in range(10):
            p=pois(lh,i)*pois(la,j)*tau(i,j,lh,la)
            if score_result(i,j,line)==pred:
                scores.append((p,i,j))
    total=sum(x[0] for x in scores)
    if not total: return top_scores(lh,la,n)
    return sorted([(p/total,i,j) for p,i,j in scores],reverse=True)[:n]

def confidence_bin(c):
    return "0.33-0.40" if c<.40 else "0.40-0.50" if c<.50 else "0.50-0.60" if c<.60 else "0.60-0.70" if c<.70 else "0.70-0.80" if c<.80 else "0.80-0.90" if c<.90 else "0.90-1.00"

def metric(): return {"n":0,"correct":0,"brier":0.0,"logloss":0.0,"confidence_sum":0.0}
def add(d,pred,actual,p):
    y={k:0 for k in p}; y[actual]=1
    d["n"]+=1; d["correct"]+=int(pred==actual)
    d["brier"]+=sum((p[k]-y[k])**2 for k in p)
    d["logloss"]-=math.log(max(1e-12,p[actual]))
    d["confidence_sum"]+=max(p.values())
def finish(d):
    n=d["n"]
    return {"n":n,"accuracy":round(d["correct"]/n,4) if n else None,
            "brier":round(d["brier"]/n,4) if n else None,
            "logloss":round(d["logloss"]/n,4) if n else None,
            "mean_confidence":round(d["confidence_sum"]/n,4) if n else None,
            "confidence_hit_gap":round(d["confidence_sum"]/n-d["correct"]/n,4) if n else None}

def fit_class_prior(train, alpha=PRIOR_ALPHA):
    counts=Counter(actual for _,actual in train)
    total=sum(counts.values())
    classes=("H","D","A")
    return {k:(counts[k]+alpha)/(total+alpha*len(classes)) for k in classes}

def apply_class_prior(p, prior, weight=PRIOR_WEIGHT):
    q={k:p[k]*max(prior[k],1e-12)**weight for k in p}
    s=sum(q.values())
    return {k:v/s for k,v in q.items()} if s else p

def fit_line_priors(samples, split):
    train=samples[:split]
    pooled=fit_class_prior([(s["p"],s["actual"]) for s in train])
    by=defaultdict(list)
    for s in train: by[s["line"]].append((s["p"],s["actual"]))
    priors={}
    for line,items in by.items():
        priors[line]=fit_class_prior(items) if len(items)>=20 else pooled
    return pooled,priors

def main():
    con=sqlite3.connect(DB)
    source_sql = """
      SELECT sm.match_id,sm.handicap,sm.pool_code,r.ft_home,r.ft_away,
             c.competition_code,m.kickoff
      FROM sporttery_market sm
      JOIN results r ON r.match_id=sm.match_id
      JOIN matches m ON m.match_id=sm.match_id
      JOIN competitions c ON c.competition_id=m.competition_id
      WHERE sm.handicap IS NOT NULL
        AND sm.pool_code IN ({})
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL AND m.status='finished'
    """.format(",".join("?" for _ in SAMPLE_SOURCES))
    params=list(SAMPLE_SOURCES)
    if BASIS=="sporttery":
        source_sql=source_sql.replace(
            "AND r.ft_home IS NOT NULL",
            "AND sm.source_status = 'sporttery_official_result_endpoint' AND r.ft_home IS NOT NULL"
        )
    raw=con.execute(source_sql+" ORDER BY m.kickoff,sm.match_id",params).fetchall()

    chosen={}
    for mid,line,pool,fh,fa,league,ko in raw:
        line=canonical_line(line)
        if line is None or line not in TARGET_LINES: continue
        key=(mid,line)
        if key not in chosen or PRIORITY.get(pool,0)>PRIORITY.get(chosen[key][2],0):
            chosen[key]=(mid,line,pool,fh,fa,league,ko)

    out_models={}
    line_sample_counts={str(k):0 for k in sorted(TARGET_LINES)}
    for mid,line,pool,fh,fa,league,ko in chosen.values():
        if line in TARGET_LINES: line_sample_counts[str(line)]+=1
    for model in ("v3","v4"):
        samples=[]
        audit={"n":0,"top1_consistent":0,"top2_consistent":0,"both_consistent":0,"inconsistent_examples":[]}
        for mid,line,pool,fh,fa,league,ko in chosen.values():
            match=con.execute("""
              SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
              FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.match_id=?
            """,(mid,)).fetchone()
            if not match: continue
            try:
                lh,la=model_lambdas(con,model,match)
                p=probs_for_line(lh,la,line)
            except Exception: continue
            pred=max(p,key=p.get)
            # Audit the actual top-2 score modes from the same score distribution.
            # Do NOT filter scores by pred first: that would make every score
            # mechanically consistent with pred and hide score->handicap errors.
            scores=top_scores(lh,la,2)
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
            samples.append({"mid":mid,"line":line,"pool":pool,"league":league,"kickoff":ko,
                            "p":p,"actual":actual_for_line(fh,fa,line)})

        samples.sort(key=lambda x:(x["kickoff"],x["mid"]))
        split=max(1,int(len(samples)*.60))
        train=[(s["p"],s["actual"]) for s in samples[:split]]
        global_t=fit_temperature(train) if train else 1.0
        line_train=defaultdict(list)
        for s in samples[:split]: line_train[s["line"]].append((s["p"],s["actual"]))
        line_t={line:(fit_temperature(items) if len(items)>=20 else global_t) for line,items in line_train.items()}
        pooled_prior,line_priors=fit_line_priors(samples,split)

        raw_m=metric(); cal_m=metric(); prior_m=metric()
        raw_bins=defaultdict(metric); cal_bins=defaultdict(metric); prior_bins=defaultdict(metric)
        by_line=defaultdict(lambda:{"raw":metric(),"calibrated":metric(),"prior_calibrated":metric()})
        by_source=defaultdict(lambda:{"raw":metric(),"calibrated":metric(),"prior_calibrated":metric()})

        for s in samples[split:]:
            pr=s["p"]
            pc=apply_temperature(pr,line_t.get(s["line"],global_t))
            pp=apply_class_prior(pc,line_priors.get(s["line"],pooled_prior))
            pred_r=max(pr,key=pr.get); pred_c=max(pc,key=pc.get); pred_p=max(pp,key=pp.get)
            add(raw_m,pred_r,s["actual"],pr); add(cal_m,pred_c,s["actual"],pc); add(prior_m,pred_p,s["actual"],pp)
            add(raw_bins[confidence_bin(max(pr.values()))],pred_r,s["actual"],pr)
            add(cal_bins[confidence_bin(max(pc.values()))],pred_c,s["actual"],pc)
            add(prior_bins[confidence_bin(max(pp.values()))],pred_p,s["actual"],pp)
            add(by_line[s["line"]]["raw"],pred_r,s["actual"],pr)
            add(by_line[s["line"]]["calibrated"],pred_c,s["actual"],pc)
            add(by_line[s["line"]]["prior_calibrated"],pred_p,s["actual"],pp)
            add(by_source[s["pool"]]["raw"],pred_r,s["actual"],pr)
            add(by_source[s["pool"]]["calibrated"],pred_c,s["actual"],pc)
            add(by_source[s["pool"]]["prior_calibrated"],pred_p,s["actual"],pp)

        # Evaluation-only sensitivity sweep for high-volume ±1 lines.
        # Report all fixed weights; never select a weight on the test slice.
        prior_weight_sweep={}
        for sweep_w in (0.0,0.25,0.5,0.75,1.0):
            sweep_by_line={}
            for target_line in (-1,1):
                mm=metric()
                for s in samples[split:]:
                    if s["line"]!=target_line: continue
                    pc=apply_temperature(s["p"],line_t.get(s["line"],global_t))
                    pp=apply_class_prior(pc,line_priors.get(s["line"],pooled_prior),weight=sweep_w)
                    add(mm,max(pp,key=pp.get),s["actual"],pp)
                sweep_by_line[str(target_line)]=finish(mm)
            prior_weight_sweep[str(sweep_w)]=sweep_by_line

        # Expanding-window rolling validation: never use future rows for fitting.
        # Four chronological test windows; each fold expands the training set.
        rolling_folds=[]
        n=len(samples)
        min_train=max(30,int(n*0.40))
        remaining=n-min_train
        if remaining>0:
            chunk=max(10,remaining//4)
            starts=list(range(min_train,n,chunk))
            for fi,start in enumerate(starts[:4],1):
                end=min(n,start+chunk)
                if end<=start: continue
                train_slice=samples[:start]
                test_slice=samples[start:end]
                tr=[(s["p"],s["actual"]) for s in train_slice]
                gt=fit_temperature(tr) if tr else 1.0
                lt_train=defaultdict(list)
                for s in train_slice: lt_train[s["line"]].append((s["p"],s["actual"]))
                lt={line:(fit_temperature(items) if len(items)>=20 else gt) for line,items in lt_train.items()}
                pooled,lp=fit_line_priors(samples,start)
                fold_raw=metric(); fold_cal=metric(); fold_prior=metric()
                fold_pm1={}
                for target_line in (-1,1):
                    fold_pm1[str(target_line)]={}
                    for w in (0.0,0.25,0.5,0.75,1.0):
                        mm=metric()
                        for s in test_slice:
                            if s["line"]!=target_line: continue
                            pc=apply_temperature(s["p"],lt.get(s["line"],gt))
                            pp=apply_class_prior(pc,lp.get(s["line"],pooled),weight=w)
                            add(mm,max(pp,key=pp.get),s["actual"],pp)
                        fold_pm1[str(target_line)][str(w)]=finish(mm)
                for s in test_slice:
                    pr=s["p"]; pc=apply_temperature(pr,lt.get(s["line"],gt))
                    pp=apply_class_prior(pc,lp.get(s["line"],pooled))
                    add(fold_raw,max(pr,key=pr.get),s["actual"],pr)
                    add(fold_cal,max(pc,key=pc.get),s["actual"],pc)
                    add(fold_prior,max(pp,key=pp.get),s["actual"],pp)
                rolling_folds.append({
                    "fold":fi,"train_n":len(train_slice),"test_n":len(test_slice),
                    "train_end":train_slice[-1]["kickoff"] if train_slice else None,
                    "test_start":test_slice[0]["kickoff"] if test_slice else None,
                    "test_end":test_slice[-1]["kickoff"] if test_slice else None,
                    "raw":finish(fold_raw),"calibrated":finish(fold_cal),
                    "prior_calibrated":finish(fold_prior),"pm1_prior_weight_sweep":fold_pm1
                })

        out_models[model]={
            "eligible_market_rows":len(raw),"deduped_match_line_rows":len(chosen),"test_rows":len(samples)-split,
            "effective_sample_target_per_integer_line":200,
            "effective_sample_min_target_by_integer_line":{"-3":100,"-2":100,"-1":200,"1":200,"2":100,"3":100},
            "effective_sample_count_by_integer_line":line_sample_counts,
            "effective_sample_target_met":all(line_sample_counts.get(k,0)>=t for k,t in {-3:100,-2:100,-1:200,1:200,2:100,3:100}.items()),
            "raw":finish(raw_m),"calibrated":finish(cal_m),"prior_calibrated":finish(prior_m),
            "temperature_global":global_t,"temperature_by_integer_line":{str(k):v for k,v in sorted(line_t.items())},
            "nonzero_class_prior_weight":PRIOR_WEIGHT,"nonzero_class_prior_alpha":PRIOR_ALPHA,
            "nonzero_pooled_class_prior":pooled_prior,
            "nonzero_line_prior_training_n":{str(k):len(v) for k,v in sorted(line_train.items())},
            "confidence_calibration_raw":{k:finish(v) for k,v in sorted(raw_bins.items())},
            "confidence_calibration_calibrated":{k:finish(v) for k,v in sorted(cal_bins.items())},
            "confidence_calibration_prior_calibrated":{k:finish(v) for k,v in sorted(prior_bins.items())},
            "by_line":{str(k):{"raw":finish(v["raw"]),"calibrated":finish(v["calibrated"]),"prior_calibrated":finish(v["prior_calibrated"])} for k,v in sorted(by_line.items())},
            "by_source":{k:{"raw":finish(v["raw"]),"calibrated":finish(v["calibrated"]),"prior_calibrated":finish(v["prior_calibrated"])} for k,v in sorted(by_source.items())},
            "score_mapping_audit":audit,
            "pm1_prior_weight_sweep":prior_weight_sweep,
            "rolling_expanding_validation":rolling_folds
        }

    payload={"models":out_models,
      "definition":("Leakage-safe integer handicap evaluation using the China Sports Lottery (中国体育彩票/中国竞彩网) 让球胜平负 handicap as an independent validation sample."
    if BASIS=="sporttery" else
    "Leakage-safe integer handicap evaluation using Asian handicap market sources as the main sample, with deterministic source priority and one row per match+integer line; China Sports Lottery hhad is excluded from the main sample."),
      "mapping":{"sporttery_home_perspective":"goalLine is interpreted as the published home-perspective handicap: home -1 => line=-1; home +2 (away gives 2) => line=+2","settlement":"H if home_goals+line>away_goals; D if equal; A if lower","quarter_and_half_lines":"excluded from this 3-way integer module"},
      "sample_basis":BASIS,"market_priority":list(SAMPLE_SOURCES),
      "calibration":"Temperature fitted on first 60% chronologically; evaluated on later 40%. Nonzero class-prior correction is fitted only on the chronological training window with additive smoothing and fixed shrinkage weight; line-specific prior requires >=20 training samples, otherwise pooled nonzero-line prior is used.",
      "notes":[("Sample-defining source is strictly China Sports Lottery hhad; Asian-book/SofaScore/SGOdds lines are excluded from this independent validation." if BASIS=="sporttery" else "Main sample uses Asian handicap sources only; Sporttery hhad is excluded and retained as an independent validation benchmark."),"V3 remains production baseline candidate; V4 remains experimental.","Target calibration excludes level-ball line=0; level-ball rows remain stored but are not used for this handicap target.","Handicap is evaluation context, not a direct prediction feature.","Model confidence and empirical hit rate are reported separately.","Prior-calibrated results are evaluation-only until they beat the baseline on chronological holdout."]}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
