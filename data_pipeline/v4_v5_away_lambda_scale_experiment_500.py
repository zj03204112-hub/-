#!/usr/bin/env python3
"""Test one lambda-generation module: away-goal lambda scale only, frozen chronological 500."""
import csv,json,math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import v4_v5_joint_engine_validation_500 as base
OUT_JSON=ROOT/"model_validation/v4_v5_away_lambda_scale_experiment_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_away_lambda_scale_experiment_500.csv"
W=0.65
SCALES=[round(0.70+i*0.025,3) for i in range(17)] # 0.70..1.10

def setup(x,scale=1.0):
    lh=math.exp(W*math.log(max(1e-9,x["v4"][0]))+(1-W)*math.log(max(1e-9,x["v5"][0])))
    la=math.exp(W*math.log(max(1e-9,x["v4"][1]))+(1-W)*math.log(max(1e-9,x["v5"][1])))*scale
    return lh,la,base.matrix(lh,la)
def actual(x):
    h,a=int(x["match"][4]),int(x["match"][5])
    return h,a,"H" if h>a else "D" if h==a else "A"
def score(xs,scale=1.0):
    n=len(xs); correct=tp=fp=fn=0; brier=logloss=snll=home_nll=away_nll=0.0; detail=[]; counts={}
    actual_away=[]; lambda_away=[]; actual_home=[]; lambda_home=[]
    for x in xs:
        h,a,act=actual(x); lh,la,m=setup(x,scale); p=base.matrix_probs(m); pred=max(p,key=p.get)
        correct+=pred==act;tp+=pred=="D" and act=="D";fp+=pred=="D" and act!="D";fn+=pred!="D" and act=="D"
        brier+=base.brier(p,act);logloss+=base.logloss(p,act);snll+=base.score_nll(m,(h,a))
        def pnll(k,l):return l-k*math.log(max(l,1e-12))+math.lgamma(k+1)
        home_nll+=pnll(h,lh);away_nll+=pnll(a,la);actual_home.append(h);lambda_home.append(lh);actual_away.append(a);lambda_away.append(la)
        cls=("correct_home_win" if act=="H" and pred=="H" else "incorrect_home_win" if act=="H" else
             "correct_away_win" if act=="A" and pred=="A" else "incorrect_away_win" if act=="A" else
             "correct_draw" if pred=="D" else "missed_draw")
        counts[cls]=counts.get(cls,0)+1
        detail.append({"match_id":x["match"][0],"kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
          "actual_score":f"{h}-{a}","actual_90":act,"pred_90":pred,"classification":cls,
          "lambda_home":round(lh,5),"lambda_away":round(la,5),"away_lambda_scale":scale,
          "p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),
          "score_nll":round(base.score_nll(m,(h,a)),6),"brier":round(base.brier(p,act),6),"logloss":round(base.logloss(p,act),6)})
    return {"n":n,"correct":correct,"accuracy":correct/n if n else 0,"draw_tp":tp,"draw_fp":fp,"draw_fn":fn,
      "draw_recall":tp/(tp+fn) if tp+fn else 0,"draw_precision":tp/(tp+fp) if tp+fp else 0,
      "brier":brier/n if n else 0,"logloss":logloss/n if n else 0,"score_nll":snll/n if n else 0,
      "home_goal_nll":home_nll/n if n else 0,"away_goal_nll":away_nll/n if n else 0,
      "mean_actual_home_goals":sum(actual_home)/n,"mean_lambda_home":sum(lambda_home)/n,
      "mean_actual_away_goals":sum(actual_away)/n,"mean_lambda_away":sum(lambda_away)/n,
      "class_counts":counts},detail
def main():
    rows=base.get_rows();rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    baseline_cal,_=score(cal,1.0); candidates=[]
    for s in SCALES:
        m,_=score(cal,s);candidates.append({"scale":s,**m})
    best=min(candidates,key=lambda r:(r["score_nll"],abs(r["scale"]-1.0)))
    base_test,bd=score(test,1.0);exp_test,ed=score(test,best["scale"])
    accepted=bool(best["scale"]!=1.0 and exp_test["accuracy"]>base_test["accuracy"] and
      exp_test["draw_recall"]>=base_test["draw_recall"] and exp_test["brier"]<=base_test["brier"] and
      exp_test["logloss"]<=base_test["logloss"] and exp_test["score_nll"]<base_test["score_nll"])
    out={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":len(rows),"split":{"train":len(train),"calibration":len(cal),"final_holdout":len(test)},
      "diagnosis":"Away lambda is more overestimated than home lambda on train/calibration and remains overestimated on final holdout; change only away lambda.",
      "module":"away lambda scale only; home lambda, model features, score matrix formula and classifier unchanged",
      "selection":"calibration 100 only; minimize exact-score NLL; final holdout never used for tuning",
      "calibration_original":baseline_cal,"selected_away_lambda_scale":best["scale"],
      "calibration_selected":{k:v for k,v in best.items() if k!="scale"},"calibration_candidates":candidates,
      "final_holdout_original":base_test,"final_holdout_experiment":exp_test,
      "deltas_experiment_minus_original":{"accuracy":exp_test["accuracy"]-base_test["accuracy"],
        "draw_recall":exp_test["draw_recall"]-base_test["draw_recall"],"brier":exp_test["brier"]-base_test["brier"],
        "logloss":exp_test["logloss"]-base_test["logloss"],"score_nll":exp_test["score_nll"]-base_test["score_nll"],
        "away_goal_nll":exp_test["away_goal_nll"]-base_test["away_goal_nll"]},
      "acceptance_gate":{"accuracy_must_strictly_improve":True,"draw_recall_must_not_decline":True,
        "brier_logloss_must_not_worsen":True,"score_nll_must_improve":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK","production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    with OUT_JSON.open("w",encoding="utf-8") as f:json.dump(out,f,ensure_ascii=False,indent=2)
    for r in bd:r["split"]="final_holdout";r["arm"]="original"
    for r in ed:r["split"]="final_holdout";r["arm"]="away_lambda_scale_experiment"
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(bd[0].keys()));wr.writeheader();wr.writerows(bd+ed)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
