#!/usr/bin/env python3
"""Conditional draw-probability calibration experiment; changes probability mapping only.
Trains on first 300 frozen matches, selects regularization on next 100, evaluates final 100 untouched.
Never changes lambda generation or production model.
"""
import csv, json, math, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "model_validation/v4_v5_correct_win_loss_common_causes_500.csv"
OUT_JSON = ROOT / "model_validation/v4_v5_conditional_draw_probability_500.json"
OUT_CSV = ROOT / "model_validation/v4_v5_conditional_draw_probability_500.csv"
EPS = 1e-8
FEATURES = ["base_draw_logit", "abs_lambda_gap", "lambda_total", "abs_strength_gap",
            "form5_points_gap", "rest_days_gap"]
REGS = [0.001, 0.01, 0.1, 1.0, 10.0]

def number(row, key, default=0.0):
    try:
        v = float(row.get(key, ""))
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default

def sigmoid(z):
    if z >= 0:
        e = math.exp(-min(z, 40))
        return 1.0 / (1.0 + e)
    e = math.exp(max(z, -40))
    return e / (1.0 + e)

def logit(p):
    p = min(1-EPS, max(EPS, p))
    return math.log(p/(1-p))

def raw_features(r):
    return [
        logit(number(r, "p_draw", 1/3)),
        abs(number(r, "lambda_gap")),
        number(r, "lambda_total"),
        abs(number(r, "strength_gap")),
        number(r, "home_form5_points") - number(r, "away_form5_points"),
        number(r, "home_rest_days") - number(r, "away_rest_days")
    ]

def standardize(train_x, rows_x):
    means = [sum(x[j] for x in train_x)/len(train_x) for j in range(len(FEATURES))]
    scales = []
    for j, m in enumerate(means):
        var = sum((x[j]-m)**2 for x in train_x)/len(train_x)
        scales.append(math.sqrt(var) if var > 1e-10 else 1.0)
    def transform(x):
        return [1.0] + [(x[j]-means[j])/scales[j] for j in range(len(FEATURES))]
    return [transform(x) for x in rows_x], means, scales

def fit_logistic(X, y, reg, steps=3500, lr=0.04):
    beta = [0.0]*len(X[0])
    n = len(X)
    for _ in range(steps):
        grad = [0.0]*len(beta)
        for xi, yi in zip(X, y):
            p = sigmoid(sum(a*b for a,b in zip(beta, xi)))
            err = p-yi
            for j, val in enumerate(xi):
                grad[j] += err*val/n
        for j in range(1, len(beta)):
            grad[j] += reg*beta[j]
        beta = [b-lr*g for b,g in zip(beta, grad)]
    return beta

def predict_prob(beta, x):
    return sigmoid(sum(a*b for a,b in zip(beta, x)))

def probabilities(row, p_draw):
    ph = max(EPS, number(row, "p_home"))
    pa = max(EPS, number(row, "p_away"))
    total = ph+pa
    remain = 1.0-p_draw
    return {"H": remain*ph/total, "D": p_draw, "A": remain*pa/total}

def metrics(rows, ps):
    n=len(rows); correct=tp=fp=fn=0; brier=ll=0.0
    for r,p in zip(rows,ps):
        actual=r["actual_90"]
        pred=max(p, key=p.get)
        correct += int(pred==actual)
        tp += int(pred=="D" and actual=="D")
        fp += int(pred=="D" and actual!="D")
        fn += int(pred!="D" and actual=="D")
        brier += sum((p[k]-(1.0 if actual==k else 0.0))**2 for k in ("H","D","A"))
        ll -= math.log(max(EPS,p[actual]))
    return {"n":n,"correct":correct,"accuracy":correct/n if n else 0,
            "draw_tp":tp,"draw_fp":fp,"draw_fn":fn,
            "draw_precision":tp/(tp+fp) if tp+fp else 0,
            "draw_recall":tp/(tp+fn) if tp+fn else 0,
            "brier":brier/n if n else None,"logloss":ll/n if n else None}

def main():
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        rows=list(csv.DictReader(f))
    rows.sort(key=lambda r:(r["kickoff"],r["match_id"]))
    if len(rows) != 500:
        raise RuntimeError(f"Expected frozen 500 rows, got {len(rows)}")
    train, cal, test = rows[:300], rows[300:400], rows[400:]
    Xraw=[raw_features(r) for r in rows]
    Xtrain, means, scales=standardize(Xraw[:300], Xraw)
    ytrain=[int(r["actual_90"]=="D") for r in train]
    grid=[]
    candidates=[]
    for reg in REGS:
        beta=fit_logistic(Xtrain[:300], ytrain, reg)
        pcal=[probabilities(r,predict_prob(beta,x)) for r,x in zip(cal,Xtrain[300:400])]
        m=metrics(cal,pcal)
        grid.append({"l2":reg,**m})
        candidates.append((m["logloss"],reg,beta,m))
    _, selected_reg, beta, selected_cal=min(candidates,key=lambda t:(t[0],t[1]))
    ptest_cal=[probabilities(r,predict_prob(beta,x)) for r,x in zip(test,Xtrain[400:])]
    base_test=[{"H":number(r,"p_home"),"D":number(r,"p_draw"),"A":number(r,"p_away")} for r in test]
    base_cal=[{"H":number(r,"p_home"),"D":number(r,"p_draw"),"A":number(r,"p_away")} for r in cal]
    result={
      "status":"EXPERIMENT_ONLY",
      "module_changed":"conditional probability distribution only; lambda generation and base H/A ratio unchanged",
      "sample_n":500,"split":{"train_n":300,"calibration_n":100,"final_holdout_n":100},
      "features":FEATURES,"missing_numeric_values":"blank values are treated as 0; interpret cautiously",
      "selected_l2":selected_reg,"calibration_grid":grid,
      "calibration_baseline":metrics(cal,base_cal),
      "calibration_conditional":selected_cal,
      "final_holdout_baseline":metrics(test,base_test),
      "final_holdout_conditional":metrics(test,ptest_cal),
      "holdout_delta":{
        "accuracy":metrics(test,ptest_cal)["accuracy"]-metrics(test,base_test)["accuracy"],
        "draw_recall":metrics(test,ptest_cal)["draw_recall"]-metrics(test,base_test)["draw_recall"],
        "brier":metrics(test,ptest_cal)["brier"]-metrics(test,base_test)["brier"],
        "logloss":metrics(test,ptest_cal)["logloss"]-metrics(test,base_test)["logloss"]
      },
      "promotion_rule":"No production promotion unless holdout accuracy and draw recall improve and Brier/logloss do not materially worsen."
    }
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        fields=["match_id","kickoff","home","away","actual_90","baseline_pred","conditional_pred",
                "baseline_p_home","baseline_p_draw","baseline_p_away",
                "conditional_p_home","conditional_p_draw","conditional_p_away","split"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for i,r in enumerate(rows):
            split="train" if i<300 else "calibration" if i<400 else "final_holdout"
            pbase={"H":number(r,"p_home"),"D":number(r,"p_draw"),"A":number(r,"p_away")}
            pnew=probabilities(r,predict_prob(beta,Xtrain[i]))
            w.writerow({"match_id":r["match_id"],"kickoff":r["kickoff"],"home":r["home"],"away":r["away"],
              "actual_90":r["actual_90"],"baseline_pred":max(pbase,key=pbase.get),"conditional_pred":max(pnew,key=pnew.get),
              "baseline_p_home":pbase["H"],"baseline_p_draw":pbase["D"],"baseline_p_away":pbase["A"],
              "conditional_p_home":pnew["H"],"conditional_p_draw":pnew["D"],"conditional_p_away":pnew["A"],"split":split})
    OUT_JSON.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
