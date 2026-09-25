import json, sqlite3, os, sys
sys.path.insert(0, "data_pipeline")
from backtest_player_10 import outcome, handicap_probs, model_lambdas, player_enhanced

DB="football_model_database.sqlite"
OUT=os.environ.get("PLAYER_CALIBRATION_OUT","data/player_layer_calibration_300.json")
N=int(os.environ.get("PLAYER_CALIBRATION_N","300"))
TRAIN_N=int(os.environ.get("PLAYER_CALIBRATION_TRAIN_N","200"))
COEFFS=[float(x) for x in os.environ.get("PLAYER_COEFFS","0.006,0.009,0.012,0.015,0.018,0.021,0.024").split(",")]

def main():
    con=sqlite3.connect(DB)
    matches=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      JOIN provider_event_map pem ON pem.match_id=m.match_id AND pem.provider='fotmob'
      WHERE m.status='finished' AND r.ft_home IS NOT NULL
      ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?""",(max(N*5,50),)).fetchall()
    eligible=[]
    for m in matches:
        line=con.execute("""SELECT handicap FROM sporttery_market WHERE match_id=? AND handicap IS NOT NULL ORDER BY captured_at ASC LIMIT 1""",(m[0],)).fetchone()
        if line: eligible.append((m,float(line[0])))
        if len(eligible)>=N: break
    if len(eligible)<N: raise SystemExit(f"PLAYER_CALIBRATION_NEEDS_{N}:{len(eligible)}")
    # eligible is newest-first; use older 200 for coefficient selection and newest 100 as untouched holdout.
    holdout_n=N-TRAIN_N
    train=list(reversed(eligible[holdout_n:]))
    holdout=list(reversed(eligible[:holdout_n]))
    def evaluate(items,coeff):
        one=hand=0
        for m,line in items:
            lh,la=model_lambdas(con,"v3",m,use_lineup=False)
            p=handicap_probs(lh,la,line)[0]
            plh,pla,_,_,_,_=player_enhanced(con,m,coeff)
            pp=handicap_probs(plh,pla,line)[0]
            actual=outcome(m)
            # 1X2 from the same lambda layer via handicap_probs at 0 line is equivalent to 3-way probabilities.
            one += int(max(p,key=p.get)==actual)
            ah="H" if (m[4]-m[5])+line>0 else "D" if abs((m[4]-m[5])+line)<1e-9 else "A"
            hand += int(max(pp,key=pp.get)==ah)
        return {"one_x2_accuracy":round(one/len(items),4),"handicap_accuracy":round(hand/len(items),4),"n":len(items)}
    rows=[]
    for coeff in COEFFS:
        rows.append({"coefficient":coeff,"train":evaluate(train,coeff),"holdout":evaluate(holdout,coeff)})
    # Select only on training handicap accuracy; ties prefer smaller absolute correction.
    best=max(rows,key=lambda x:(x["train"]["handicap_accuracy"],x["train"]["one_x2_accuracy"],-x["coefficient"]))
    result={"definition":"Leakage-safe player correction coefficient calibration. Coefficient selection uses only the older training block; the newer holdout block is evaluated once after selection. Target-match player data are never used.","n":N,"train_n":TRAIN_N,"holdout_n":holdout_n,"coefficients":rows,"selected_coefficient":best["coefficient"],"selected_train":best["train"],"selected_holdout":best["holdout"]}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    con.close()
if __name__=="__main__": main()
