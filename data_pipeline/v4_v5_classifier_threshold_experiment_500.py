#!/usr/bin/env python3
"""Single-module final-classifier threshold experiment on frozen 500; lambdas/probabilities stay fixed."""
import csv,json,math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import v4_v5_joint_engine_validation_500 as base
OUT_JSON=ROOT/"model_validation/v4_v5_classifier_threshold_experiment_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_classifier_threshold_experiment_500.csv"
W=0.65
DELTAS=[round(i*0.005,3) for i in range(41)] # 0..0.20

def get_probs(x):
    lh=math.exp(W*math.log(max(1e-9,x["v4"][0]))+(1-W)*math.log(max(1e-9,x["v5"][0])))
    la=math.exp(W*math.log(max(1e-9,x["v4"][1]))+(1-W)*math.log(max(1e-9,x["v5"][1])))
    m=base.matrix(lh,la)
    return lh,la,m,base.matrix_probs(m)
def actual(x):
    h,a=int(x["match"][4]),int(x["match"][5])
    return "H" if h>a else "D" if h==a else "A"
def predict(p,delta):
    ha=max(p["H"],p["A"])
    if p["D"] >= ha-delta: return "D"
    return "H" if p["H"]>p["A"] else "A"
def score(xs,delta,with_detail=False):
    n=len(xs); correct=tp=fp=fn=0; brier=logloss=snll=0.0; detail=[]; counts={}
    for x in xs:
        h,a=int(x["match"][4]),int(x["match"][5]); act=actual(x)
        lh,la,m,p=get_probs(x); pred=predict(p,delta)
        correct+=pred==act; tp+=pred=="D" and act=="D"; fp+=pred=="D" and act!="D"; fn+=pred!="D" and act=="D"
        brier+=base.brier(p,act); logloss+=base.logloss(p,act); snll+=base.score_nll(m,(h,a))
        cls=("correct_home_win" if act=="H" and pred=="H" else "incorrect_home_win" if act=="H" else
             "correct_away_win" if act=="A" and pred=="A" else "incorrect_away_win" if act=="A" else
             "correct_draw" if pred=="D" else "missed_draw")
        counts[cls]=counts.get(cls,0)+1
        detail.append({"match_id":x["match"][0],"kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
          "actual_score":f"{h}-{a}","actual_90":act,"pred_90":pred,"classification":cls,"delta":delta,
          "lambda_home":round(lh,5),"lambda_away":round(la,5),"p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),
          "brier":round(base.brier(p,act),6),"logloss":round(base.logloss(p,act),6)})
    return {"n":n,"correct":correct,"accuracy":correct/n if n else 0,
      "draw_tp":tp,"draw_fp":fp,"draw_fn":fn,"draw_recall":tp/(tp+fn) if tp+fn else 0,
      "draw_precision":tp/(tp+fp) if tp+fp else 0,"brier":brier/n if n else 0,
      "logloss":logloss/n if n else 0,"score_nll":snll/n if n else 0,"class_counts":counts},detail
def main():
    rows=base.get_rows(); rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    baseline_cal,_=score(cal,0.0)
    candidates=[]
    for d in DELTAS:
        met,_=score(cal,d); candidates.append({"delta":d,**met})
    # Select only on calibration: accuracy first, draw recall as tie-breaker, then smallest threshold.
    best=max(candidates,key=lambda r:(r["accuracy"],r["draw_recall"],-r["delta"]))
    base_test,base_detail=score(test,0.0)
    exp_test,exp_detail=score(test,best["delta"])
    accepted=bool(best["delta"]>0 and exp_test["accuracy"]>base_test["accuracy"] and
                  exp_test["draw_recall"]>=base_test["draw_recall"] and
                  abs(exp_test["brier"]-base_test["brier"])<1e-12 and
                  abs(exp_test["logloss"]-base_test["logloss"])<1e-12 and
                  abs(exp_test["score_nll"]-base_test["score_nll"])<1e-12)
    # Preserve the chosen candidate only as an experiment; no production change.
    chosen_detail=exp_detail if best["delta"]>0 else base_detail
    for r in chosen_detail: r["split"]="final_holdout"; r["arm"]="classifier_threshold_experiment" if best["delta"]>0 else "original"
    out={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":len(rows),"split":{"train":len(train),"calibration":len(cal),"final_holdout":len(test)},
      "module":"final classifier only; no lambda or probability changes",
      "rule":"predict D when pD >= max(pH,pA) - delta; otherwise choose larger of pH and pA",
      "selection":"calibration 100 only; maximize accuracy, tie-break by draw recall, then smaller delta",
      "selected_delta":best["delta"],"calibration_baseline":baseline_cal,"calibration_selected":{k:v for k,v in best.items() if k!="delta"},
      "calibration_candidates":candidates,"final_holdout_original":base_test,"final_holdout_experiment":exp_test,
      "deltas_experiment_minus_original":{"accuracy":exp_test["accuracy"]-base_test["accuracy"],
        "draw_recall":exp_test["draw_recall"]-base_test["draw_recall"],"brier":exp_test["brier"]-base_test["brier"],
        "logloss":exp_test["logloss"]-base_test["logloss"],"score_nll":exp_test["score_nll"]-base_test["score_nll"]},
      "acceptance_gate":{"accuracy_must_strictly_improve":True,"draw_recall_must_not_decline":True,
        "brier_logloss_score_nll_must_remain_identical":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK",
      "production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    with OUT_JSON.open("w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(chosen_detail[0].keys()));wr.writeheader();wr.writerows(chosen_detail)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
