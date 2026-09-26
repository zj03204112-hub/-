import json, sqlite3, os, sys
sys.path.insert(0, "data_pipeline")
from backtest_player_10 import outcome, probs, handicap_probs, asian_settlement, model_lambdas, player_enhanced

DB="football_model_database.sqlite"
OUT=os.environ.get("PLAYER_CALIBRATION_OUT","data/player_layer_calibration_300.json")
N=int(os.environ.get("PLAYER_CALIBRATION_N","300"))
TRAIN_N=int(os.environ.get("PLAYER_CALIBRATION_TRAIN_N","200"))
COEFFS=[float(x) for x in os.environ.get("PLAYER_COEFFS","0.006,0.009,0.012,0.015,0.018,0.021,0.024").split(",")]
FOLDS=int(os.environ.get("PLAYER_CALIBRATION_FOLDS","4"))

def main():
    if TRAIN_N >= N or TRAIN_N < FOLDS:
        raise SystemExit(f"INVALID_CALIBRATION_SPLIT:{TRAIN_N}:{N}:{FOLDS}")
    con=sqlite3.connect(DB)
    matches=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      JOIN provider_event_map pem ON pem.match_id=m.match_id AND pem.provider='fotmob'
      WHERE m.status='finished' AND r.ft_home IS NOT NULL
      ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?""",(max(N*5,50),)).fetchall()
    eligible=[]
    for m in matches:
        line=con.execute("""SELECT handicap FROM sporttery_market WHERE match_id=? AND handicap IS NOT NULL ORDER BY captured_at ASC LIMIT 1""",(m[0],)).fetchone()
        if line:
            eligible.append((m,float(line[0])))
        if len(eligible)>=N:
            break
    if len(eligible)<N:
        raise SystemExit(f"PLAYER_CALIBRATION_NEEDS_{N}:{len(eligible)}")

    # eligible is newest-first. Keep the newest holdout completely untouched.
    holdout_n=N-TRAIN_N
    train=list(reversed(eligible[holdout_n:]))
    holdout=list(reversed(eligible[:holdout_n]))

    def evaluate(items,coeff):
        one=hand=0
        for m,line in items:
            lh,la=model_lambdas(con,"v3",m,use_lineup=False)
            p=probs(lh,la)
            plh,pla,_,_,_,_=player_enhanced(con,m,coeff)
            pp=handicap_probs(plh,pla,line)[0]
            actual=outcome(m)
            one += int(max(p,key=p.get)==actual)
            settle=asian_settlement(m[4]-m[5],line)
            ah="H" if settle.startswith("H") else "A" if settle.startswith("A") else "D"
            hand += int(max(pp,key=pp.get)==ah)
        n=len(items)
        return {"one_x2_accuracy":round(one/n,4),"handicap_accuracy":round(hand/n,4),"n":n}

    # Walk-forward calibration inside the older training block.
    # Each fold validates on a later chronological block, never on earlier data.
    fold_size=TRAIN_N//FOLDS
    if fold_size < 10:
        raise SystemExit(f"CALIBRATION_FOLD_TOO_SMALL:{fold_size}")
    folds=[]
    for i in range(FOLDS):
        start=i*fold_size
        end=(i+1)*fold_size if i<FOLDS-1 else TRAIN_N
        valid=train[start:end]
        prior=train[:start]
        folds.append((prior,valid))

    rows=[]
    for coeff in COEFFS:
        fold_results=[]
        for fold_index,(prior,valid) in enumerate(folds,1):
            # The player coefficient is fixed and not fitted on the target validation block.
            # prior is recorded to make the time ordering explicit; coefficient candidates
            # are compared only on each later validation block.
            vr=evaluate(valid,coeff)
            fold_results.append({"fold":fold_index,"train_prior_n":len(prior),"validation":vr})
        total_n=sum(x["validation"]["n"] for x in fold_results)
        total_hand=sum(x["validation"]["handicap_accuracy"]*x["validation"]["n"] for x in fold_results)
        total_one=sum(x["validation"]["one_x2_accuracy"]*x["validation"]["n"] for x in fold_results)
        rows.append({
            "coefficient":coeff,
            "walk_forward_train": {
                "one_x2_accuracy":round(total_one/total_n,4),
                "handicap_accuracy":round(total_hand/total_n,4),
                "n":total_n,
                "folds":fold_results
            }
        })

    # Select only from the older 200-match walk-forward validation.
    # Handicap is primary because the production task is handicap prediction;
    # 1X2 is the tie-breaker, then smaller correction for stability.
    best=max(rows,key=lambda x:(
        x["walk_forward_train"]["handicap_accuracy"],
        x["walk_forward_train"]["one_x2_accuracy"],
        -x["coefficient"]
    ))
    selected=best["coefficient"]
    selected_holdout=evaluate(holdout,selected)

    result={
        "definition":"Leakage-safe player correction coefficient calibration. Candidate selection uses only walk-forward validation inside the older training block; the newest holdout block is evaluated once after selection. Target-match player data are never used.",
        "n":N,
        "train_n":TRAIN_N,
        "holdout_n":holdout_n,
        "folds":FOLDS,
        "fold_size":fold_size,
        "coefficients":rows,
        "selected_coefficient":selected,
        "selected_walk_forward_train":best["walk_forward_train"],
        "selected_holdout":selected_holdout
    }
    with open(OUT,"w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    con.close()

if __name__=="__main__":
    main()
