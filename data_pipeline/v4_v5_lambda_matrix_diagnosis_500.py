#!/usr/bin/env python3
"""Diagnose lambda and score matrix on the frozen 500; test one lambda-scale change only."""
import csv, json, math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import v4_v5_joint_engine_validation_500 as base

OUT_JSON=ROOT/"model_validation/v4_v5_lambda_matrix_diagnosis_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_lambda_matrix_diagnosis_500.csv"
RHO=base.RHO
N=base.N
W=0.65
SCALES=[round(0.75+i*0.025,3) for i in range(17)] # 0.75..1.15

def lambdas(x):
    return (math.exp(W*math.log(max(1e-9,x["v4"][0]))+(1-W)*math.log(max(1e-9,x["v5"][0]))),
            math.exp(W*math.log(max(1e-9,x["v4"][1]))+(1-W)*math.log(max(1e-9,x["v5"][1]))))
def actual(x):
    return int(x["match"][4]),int(x["match"][5])
def outcome(h,a): return "H" if h>a else "D" if h==a else "A"
def pscore(lh,la,h,a):
    if h>=N or a>=N: return 1e-12
    return max(1e-12,base.matrix(lh,la)[h][a])
def metrics(xs,scale=1.0):
    n=len(xs); correct=0; tp=fp=fn=0; bs=ll=snll=goal_nll=0.0
    groups={}
    detail=[]
    for x in xs:
        h,a=actual(x); lh0,la0=lambdas(x); lh,la=lh0*scale,la0*scale
        m=base.matrix(lh,la); p=base.matrix_probs(m); pred=max(p,key=p.get); act=outcome(h,a)
        correct+=pred==act; tp+=pred=="D" and act=="D"; fp+=pred=="D" and act!="D"; fn+=pred!="D" and act=="D"
        bs+=base.brier(p,act); ll+=base.logloss(p,act); snll+=base.score_nll(m,(h,a))
        # Independent Poisson marginal count NLLs; score-matrix NLL above includes DC correction.
        def pois_nll(k,l): return l-k*math.log(max(l,1e-12))+math.lgamma(k+1)
        goal_nll+=pois_nll(h,lh)+pois_nll(a,la)
        detail.append({"match_id":x["match"][0],"kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
          "actual_score":f"{h}-{a}","actual_90":act,"pred_90":pred,
          "lambda_home":round(lh,5),"lambda_away":round(la,5),"lambda_total":round(lh+la,5),
          "lambda_home_error":round(lh-h,5),"lambda_away_error":round(la-a,5),
          "p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),
          "score_nll":round(base.score_nll(m,(h,a)),6),"brier":round(base.brier(p,act),6),"logloss":round(base.logloss(p,act),6)})
    return {"n":n,"accuracy":correct/n if n else None,"correct":correct,
      "draw_tp":tp,"draw_fp":fp,"draw_fn":fn,"draw_recall":tp/(tp+fn) if tp+fn else 0,
      "draw_precision":tp/(tp+fp) if tp+fp else 0,"brier":bs/n if n else None,"logloss":ll/n if n else None,
      "score_nll":snll/n if n else None,"goal_count_nll_per_team":goal_nll/(2*n) if n else None},detail

def poisson_tail(l):
    return max(0.0,1.0-sum(math.exp(-l)*l**k/math.factorial(k) for k in range(N)))
def mean(v): return sum(v)/len(v) if v else None
def diagnostic(xs):
    rec=[]
    for x in xs:
        h,a=actual(x); lh,la=lambdas(x); p=base.matrix_probs(base.matrix(lh,la))
        pred=max(p,key=p.get); act=outcome(h,a)
        cls=("correct_home_win" if act=="H" and pred=="H" else "incorrect_home_win" if act=="H" else
             "correct_away_win" if act=="A" and pred=="A" else "incorrect_away_win" if act=="A" else
             "correct_draw" if pred=="D" else "missed_draw")
        rec.append({"x":x,"h":h,"a":a,"act":act,"pred":pred,"cls":cls,"lh":lh,"la":la,"p":p})
    summary={}
    for c in ("correct_home_win","incorrect_home_win","correct_away_win","incorrect_away_win","missed_draw","correct_draw"):
        q=[r for r in rec if r["cls"]==c]
        summary[c]={"n":len(q),"mean_actual_home_goals":mean([r["h"] for r in q]),
          "mean_actual_away_goals":mean([r["a"] for r in q]),"mean_lambda_home":mean([r["lh"] for r in q]),
          "mean_lambda_away":mean([r["la"] for r in q]),"mean_lambda_total":mean([r["lh"]+r["la"] for r in q]),
          "mean_home_lambda_error":mean([r["lh"]-r["h"] for r in q]),
          "mean_away_lambda_error":mean([r["la"]-r["a"] for r in q]),
          "mean_abs_lambda_gap":mean([abs(r["lh"]-r["la"]) for r in q]),
          "mean_p_draw":mean([r["p"]["D"] for r in q])}
    actual_goal_diff={}
    predicted_goal_diff={}
    for d in ("0","1","2","3+"):
        actual_goal_diff[d]=sum(1 for r in rec if (abs(r["h"]-r["a"])==int(d) if d!="3+" else abs(r["h"]-r["a"])>=3))
        # Expected bucket mass summed across matches, based on score matrix.
        mass=[]
        for r in rec:
            m=base.matrix(r["lh"],r["la"])
            mass.append(sum(m[i][j] for i in range(N) for j in range(N)
              if (abs(i-j)==int(d) if d!="3+" else abs(i-j)>=3)))
        predicted_goal_diff[d]=mean(mass)
    lh=[r["lh"] for r in rec]; la=[r["la"] for r in rec]
    ah=[r["h"] for r in rec]; aa=[r["a"] for r in rec]
    return {"groups":summary,"lambda_calibration":{"mean_actual_home_goals":mean(ah),"mean_lambda_home":mean(lh),
      "mean_actual_away_goals":mean(aa),"mean_lambda_away":mean(la),
      "mean_actual_total_goals":mean([h+a for h,a in zip(ah,aa)]),
      "mean_lambda_total_goals":mean([x+y for x,y in zip(lh,la)]),
      "mean_home_lambda_error":mean([x-y for x,y in zip(lh,ah)]),
      "mean_away_lambda_error":mean([x-y for x,y in zip(la,aa)]),
      "mean_truncated_poisson_mass":mean([1-(1-poisson_tail(x))*(1-poisson_tail(y)) for x,y in zip(lh,la)])},
      "goal_difference":{"actual_counts":actual_goal_diff,"mean_predicted_probability":predicted_goal_diff}}

def main():
    rows=base.get_rows()
    rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    diag={"sample_n":len(rows),"split":{"train":len(train),"calibration":len(cal),"final_holdout":len(test)},
          "chronological_diagnosis":{k:diagnostic(v) for k,v in (("all_500",rows),("train_300",train),("calibration_100",cal),("final_holdout_100",test))}}
    base_cal,_=metrics(cal,1.0)
    candidates=[]
    for s in SCALES:
        m,_=metrics(cal,s)
        candidates.append({"lambda_scale":s,**m})
    # Calibration-only selection; score NLL is the primary objective.
    best=min(candidates,key=lambda r:(r["score_nll"],abs(r["lambda_scale"]-1.0)))
    improvement=base_cal["score_nll"]-best["score_nll"]
    over_total=diag["chronological_diagnosis"]["train_300"]["lambda_calibration"]["mean_lambda_total_goals"]-diag["chronological_diagnosis"]["train_300"]["lambda_calibration"]["mean_actual_total_goals"]
    # Only test the single lambda-total scale module if calibration supports it and train diagnostics show a meaningful total-goal bias.
    run_experiment=(best["lambda_scale"]!=1.0 and improvement>=0.01 and abs(over_total)>=0.15)
    hold_base,_=metrics(test,1.0)
    hold_exp,_=metrics(test,best["lambda_scale"] if run_experiment else 1.0)
    # Acceptance: meaningful score-NLL improvement; H/D/A accuracy or draw recall must improve;
    # Brier and logloss may not deteriorate beyond small predeclared tolerances.
    accepted=bool(run_experiment and hold_exp["score_nll"]<hold_base["score_nll"] and
      hold_exp["accuracy"]>hold_base["accuracy"] and hold_exp["draw_recall"]>=hold_base["draw_recall"] and
      hold_exp["brier"]<=hold_base["brier"] and hold_exp["logloss"]<=hold_base["logloss"])
    diag["single_module_experiment"]={"module":"lambda_total_scale_only; both lambdas multiplied by same scalar, lambda ratio preserved",
      "selection_split":"calibration_100 only; final_holdout_100 never used for tuning",
      "primary_objective":"exact-score NLL","calibration_baseline":base_cal,"calibration_candidates":candidates,
      "selected_scale":best["lambda_scale"],"calibration_score_nll_improvement":improvement,
      "train_mean_lambda_total_minus_actual_total":over_total,"experiment_triggered":run_experiment,
      "final_holdout_original":hold_base,"final_holdout_experiment":hold_exp,
      "deltas_experiment_minus_original":{"accuracy":hold_exp["accuracy"]-hold_base["accuracy"],
        "draw_recall":hold_exp["draw_recall"]-hold_base["draw_recall"],"brier":hold_exp["brier"]-hold_base["brier"],
        "logloss":hold_exp["logloss"]-hold_base["logloss"],"score_nll":hold_exp["score_nll"]-hold_base["score_nll"]},
      "acceptance_gate":{"score_nll_must_improve":True,"accuracy_must_improve":True,"draw_recall_must_not_decline":True,
        "brier_must_not_worsen":True,"logloss_must_not_worsen":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK",
      "production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    with OUT_JSON.open("w",encoding="utf-8") as f: json.dump(diag,f,ensure_ascii=False,indent=2)
    # Per-match holdout comparison for traceability.
    _,bd=metrics(test,1.0); _,ed=metrics(test,best["lambda_scale"] if run_experiment else 1.0)
    for i,(b,e) in enumerate(zip(bd,ed)):
        b.update({"split":"final_holdout","arm":"original"})
        e.update({"split":"final_holdout","arm":"lambda_scale_experiment" if run_experiment else "not_triggered"})
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(bd[0].keys())); wr.writeheader(); wr.writerows(bd+ed)
    print(json.dumps(diag,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
