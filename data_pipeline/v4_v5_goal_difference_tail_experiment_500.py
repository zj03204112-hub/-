#!/usr/bin/env python3
"""Single-module goal-difference tail calibration on the frozen 500; lambdas remain unchanged."""
import csv,json,math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import v4_v5_joint_engine_validation_500 as base
OUT_JSON=ROOT/"model_validation/v4_v5_goal_difference_tail_experiment_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_goal_difference_tail_experiment_500.csv"
W=0.65
GAMMAS=[round(0.50+i*0.025,3) for i in range(41)] # 0.50..1.50

def actual(x):
    h,a=int(x["match"][4]),int(x["match"][5])
    return h,a,("H" if h>a else "D" if h==a else "A")
def matrix_for(x,gamma=1.0):
    lh=math.exp(W*math.log(max(1e-9,x["v4"][0]))+(1-W)*math.log(max(1e-9,x["v5"][0])))
    la=math.exp(W*math.log(max(1e-9,x["v4"][1]))+(1-W)*math.log(max(1e-9,x["v5"][1])))
    m=base.matrix(lh,la)
    if gamma != 1.0:
        m=[[v*(gamma if abs(i-j)>=3 else 1.0) for j,v in enumerate(row)] for i,row in enumerate(m)]
        z=sum(map(sum,m)); m=[[v/z for v in row] for row in m]
    return lh,la,m,base.matrix_probs(m)
def metrics(xs,gamma=1.0):
    n=len(xs); correct=tp=fp=fn=0; brier=logloss=snll=0.0; bucket_actual={"0":0,"1":0,"2":0,"3+":0}; bucket_pred={k:0.0 for k in bucket_actual}; detail=[]; classes={}
    for x in xs:
        h,a,act=actual(x); lh,la,m,p=matrix_for(x,gamma); pred=max(p,key=p.get)
        correct+=pred==act; tp+=pred=="D" and act=="D"; fp+=pred=="D" and act!="D"; fn+=pred!="D" and act=="D"
        brier+=base.brier(p,act); logloss+=base.logloss(p,act); snll+=base.score_nll(m,(h,a))
        d=abs(h-a); key=str(d) if d<3 else "3+"; bucket_actual[key]+=1
        for k in bucket_pred:
            bucket_pred[k]+=sum(m[i][j] for i in range(base.N) for j in range(base.N) if (str(abs(i-j))==k if k!="3+" else abs(i-j)>=3))
        cls=("correct_home_win" if act=="H" and pred=="H" else "incorrect_home_win" if act=="H" else
             "correct_away_win" if act=="A" and pred=="A" else "incorrect_away_win" if act=="A" else
             "correct_draw" if pred=="D" else "missed_draw")
        classes[cls]=classes.get(cls,0)+1
        detail.append({"match_id":x["match"][0],"kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
          "actual_score":f"{h}-{a}","actual_90":act,"pred_90":pred,"classification":cls,"gamma_tail_3plus":gamma,
          "lambda_home":round(lh,5),"lambda_away":round(la,5),"p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),
          "score_nll":round(base.score_nll(m,(h,a)),6),"brier":round(base.brier(p,act),6),"logloss":round(base.logloss(p,act),6)})
    return {"n":n,"correct":correct,"accuracy":correct/n if n else 0,"draw_tp":tp,"draw_fp":fp,"draw_fn":fn,
      "draw_recall":tp/(tp+fn) if tp+fn else 0,"draw_precision":tp/(tp+fp) if tp+fp else 0,
      "brier":brier/n if n else 0,"logloss":logloss/n if n else 0,"score_nll":snll/n if n else 0,
      "class_counts":classes,"goal_difference":{"actual_counts":bucket_actual,
      "mean_predicted_probability":{k:bucket_pred[k]/n for k in bucket_pred}}},detail
def main():
    rows=base.get_rows(); rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    base_cal,_=metrics(cal,1.0); candidates=[]
    for g in GAMMAS:
        m,_=metrics(cal,g); candidates.append({"gamma":g,**m})
    best=min(candidates,key=lambda r:(r["score_nll"],abs(r["gamma"]-1.0)))
    base_test,base_detail=metrics(test,1.0); exp_test,exp_detail=metrics(test,best["gamma"])
    accepted=bool(best["gamma"]!=1.0 and exp_test["accuracy"]>base_test["accuracy"] and
      exp_test["draw_recall"]>=base_test["draw_recall"] and exp_test["brier"]<=base_test["brier"] and
      exp_test["logloss"]<=base_test["logloss"] and exp_test["score_nll"]<base_test["score_nll"])
    out={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":len(rows),"split":{"train":len(train),"calibration":len(cal),"final_holdout":len(test)},
      "diagnosis":"Frozen-500 absolute goal-difference buckets show 3+ tail overpredicted and 1-goal margins underpredicted; test only the probability mass on |home goals-away goals| >= 3.",
      "module":"score-matrix goal-difference tail only; lambdas and classifier unchanged",
      "selection":"calibration 100 only; minimize exact-score NLL; final 100 never used for tuning",
      "calibration_original":base_cal,"selected_gamma":best["gamma"],"calibration_selected":{k:v for k,v in best.items() if k!="gamma"},
      "calibration_candidates":candidates,"final_holdout_original":base_test,"final_holdout_experiment":exp_test,
      "deltas_experiment_minus_original":{"accuracy":exp_test["accuracy"]-base_test["accuracy"],
        "draw_recall":exp_test["draw_recall"]-base_test["draw_recall"],"brier":exp_test["brier"]-base_test["brier"],
        "logloss":exp_test["logloss"]-base_test["logloss"],"score_nll":exp_test["score_nll"]-base_test["score_nll"]},
      "acceptance_gate":{"accuracy_must_strictly_improve":True,"draw_recall_must_not_decline":True,
        "brier_logloss_must_not_worsen":True,"score_nll_must_improve":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK","production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    with OUT_JSON.open("w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    _,bd=metrics(test,1.0); _,ed=metrics(test,best["gamma"])
    for r in bd:r["split"]="final_holdout";r["arm"]="original"
    for r in ed:r["split"]="final_holdout";r["arm"]="goal_difference_tail_experiment"
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(bd[0].keys()));wr.writeheader();wr.writerows(bd+ed)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
