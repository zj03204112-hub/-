#!/usr/bin/env python3
"""Experiment: improve 90-minute H/D/A accuracy with calibration-only draw logit bias.
Uses the existing frozen 500 rows and independent V5 reconstruction. Never changes production.
"""
import csv, json, math
from pathlib import Path
import v4_v5_joint_engine_validation_500 as base

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "model_validation/v4_v5_result_calibration_experiment.json"
OUT_CSV = ROOT / "model_validation/v4_v5_result_calibration_experiment.csv"

def probs_from_matrix(m):
    return base.matrix_probs(m)

def actual_score(x):
    return (int(x["match"][4]), int(x["match"][5]))

def make_matrix(x, w):
    lh = math.exp(w*math.log(max(1e-9,x["v4"][0])) + (1-w)*math.log(max(1e-9,x["v5"][0])))
    la = math.exp(w*math.log(max(1e-9,x["v4"][1])) + (1-w)*math.log(max(1e-9,x["v5"][1])))
    return base.matrix(lh, la)

def adjust_draw(p, bias):
    q = dict(p)
    q["D"] *= math.exp(bias)
    z = sum(q.values())
    return {k:v/z for k,v in q.items()}

def metrics(rows, w, bias):
    n=correct=tp=fp=fn=0
    brier_sum=ll_sum=0.0
    pred_rows=[]
    for x in rows:
        m=make_matrix(x,w)
        p=adjust_draw(probs_from_matrix(m),bias)
        pred=max(p,key=p.get); actual=x["actual"]
        n+=1; correct+=int(pred==actual)
        tp+=int(pred=="D" and actual=="D")
        fp+=int(pred=="D" and actual!="D")
        fn+=int(pred!="D" and actual=="D")
        brier_sum+=base.brier(p,actual)
        ll_sum+=base.logloss(p,actual)
        pred_rows.append({
          "match_id":x["match"]["0"] if False else x["match"][0],
          "kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
          "actual_score":f"{actual_score(x)[0]}-{actual_score(x)[1]}","actual_90":actual,
          "pred_90":pred,"p_home":p["H"],"p_draw":p["D"],"p_away":p["A"],
          "draw_logit_bias":bias
        })
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    return {"n":n,"correct":correct,"accuracy":correct/n if n else 0,
      "draw_tp":tp,"draw_fp":fp,"draw_fn":fn,"draw_precision":precision,"draw_recall":recall,
      "brier":brier_sum/n if n else None,"logloss":ll_sum/n if n else None}, pred_rows

def main():
    rows=base.get_rows()
    rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    n=len(rows); n_train=int(n*.60); n_cal=int(n*.80)
    train,cal,test=rows[:n_train],rows[n_train:n_cal],rows[n_cal:]
    # Same pre-registered blend fit: exact-score NLL on train only.
    candidates=[]
    for w in base.WEIGHTS:
        losses=[]
        for x in train:
            m=make_matrix(x,w)
            losses.append(base.score_nll(m,actual_score(x)))
        candidates.append((sum(losses)/len(losses),w))
    train_nll,w=min(candidates)
    # Fit only a one-parameter draw logit adjustment on the 100-match calibration set.
    # Primary objective: accuracy. Tie-breakers: lower logloss, then smaller adjustment.
    grid=[]
    for step in range(25):
        bias=step*0.05
        met,_=metrics(cal,w,bias)
        grid.append((met["accuracy"],-met["logloss"],-bias,bias,met))
    _,_,_,best_bias,cal_best=max(grid)
    cal_base,_=metrics(cal,w,0.0)
    test_base,base_detail=metrics(test,w,0.0)
    test_adj,adj_detail=metrics(test,w,best_bias)
    for r in base_detail: r["arm"]="joint_uncalibrated"; r["split"]="final_holdout"
    for r in adj_detail: r["arm"]="joint_draw_calibrated"; r["split"]="final_holdout"
    detail=base_detail+adj_detail
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(detail[0].keys())); wr.writeheader(); wr.writerows(detail)
    summary={
      "status":"EXPERIMENT_ONLY","sample_n":n,
      "split":{"train_n":len(train),"calibration_n":len(cal),"final_holdout_n":len(test)},
      "blend":{"v4_share":w,"v5_reconstruction_share":1-w,"train_exact_score_nll":train_nll},
      "method":"multiply draw probability odds by exp(bias), renormalize H/D/A; fit bias on calibration split only; final holdout untouched",
      "grid":[{"bias":round(step*.05,2),"calibration_accuracy":metrics(cal,w,step*.05)[0]["accuracy"],
               "calibration_logloss":metrics(cal,w,step*.05)[0]["logloss"],
               "calibration_draw_precision":metrics(cal,w,step*.05)[0]["draw_precision"],
               "calibration_draw_recall":metrics(cal,w,step*.05)[0]["draw_recall"]} for step in range(25)],
      "selected":{"draw_logit_bias":best_bias,"calibration":cal_best},
      "final_holdout":{"joint_uncalibrated":test_base,"joint_draw_calibrated":test_adj,
        "accuracy_delta":test_adj["accuracy"]-test_base["accuracy"],
        "brier_delta":test_adj["brier"]-test_base["brier"],
        "logloss_delta":test_adj["logloss"]-test_base["logloss"],
        "draw_recall_delta":test_adj["draw_recall"]-test_base["draw_recall"]},
      "promotion_gate":"No promotion unless final-holdout H/D/A accuracy improves and probability metrics are reviewed; 100 matches is preliminary."
    }
    OUT_JSON.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
