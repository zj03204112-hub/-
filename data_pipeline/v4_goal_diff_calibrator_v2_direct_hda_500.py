#!/usr/bin/env python3
"""V2 goal-difference calibration + same-holdout direct H/D/A comparison.

Experiment-only. Frozen 500 unique matches, chronological 300/100/100 split.
All tunable parameters are selected without using the final 100 labels.
No away-lambda correction is applied in this experiment.
"""
import csv, json, math, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_pipeline"))
import compare_models as cm

DB = ROOT / "football_model_database.sqlite"
MAP = ROOT / "model_validation/500_match_to_db_match_id_mapping.csv"
OUT_JSON = ROOT / "model_validation/v4_goal_diff_calibrator_v2_direct_hda_500_summary.json"
OUT_CSV = ROOT / "model_validation/v4_goal_diff_calibrator_v2_direct_hda_500_predictions.csv"
HDA = ["H", "D", "A"]
BUCKETS = ["0", "1", "2", "3+"]
EPS = 1e-9
C_GRID = [0.001, 0.01, 0.1, 1.0, 10.0]


def dt(s):
    return datetime.fromisoformat(str(s)[:19])


def actual(r):
    return "H" if r[4] > r[5] else "D" if r[4] == r[5] else "A"


def bucket(r):
    d = abs(int(r[4]) - int(r[5]))
    return "0" if d == 0 else "1" if d == 1 else "2" if d == 2 else "3+"


def recent_stats(con, team, kickoff, n):
    cutoff = (dt(kickoff) - timedelta(hours=12)).isoformat(timespec="seconds")
    hist = cm.hist(con, team, cutoff)[:n]
    if not hist:
        return [0.25, 1.15, 1.15, 0.0, 0.0]
    draws = sum(gf == ga for (gf, ga), opp in hist) / len(hist)
    gf = sum(x[0][0] for x in hist) / len(hist)
    ga = sum(x[0][1] for x in hist) / len(hist)
    gd = sum(x[0][0] - x[0][1] for x in hist) / len(hist)
    points = sum(3 if x[0][0] > x[0][1] else 1 if x[0][0] == x[0][1] else 0 for x in hist) / len(hist)
    return [draws, gf, ga, gd, points]


def probs_from_matrix(m):
    h = sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i > j)
    d = sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i == j)
    a = sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i < j)
    z = max(EPS, h + d + a)
    bucket_p = {k: 0.0 for k in BUCKETS}
    for i in range(cm.N):
        for j in range(cm.N):
            diff = abs(i - j)
            key = "0" if diff == 0 else "1" if diff == 1 else "2" if diff == 2 else "3+"
            bucket_p[key] += m[i][j]
    bz = max(EPS, sum(bucket_p.values()))
    return {"H": h/z, "D": d/z, "A": a/z}, {k: max(EPS, v/bz) for k,v in bucket_p.items()}, [m[0][0], m[1][1], m[2][2]]


def metric_block(y, probs, labels):
    arr = np.asarray(probs, dtype=float)
    arr = np.clip(arr, EPS, 1.0)
    arr /= arr.sum(axis=1, keepdims=True)
    pred = [labels[i] for i in arr.argmax(axis=1)]
    onehot = np.zeros_like(arr)
    idx = {c:i for i,c in enumerate(labels)}
    for i, v in enumerate(y):
        onehot[i, idx[v]] = 1.0
    per = {}
    for c in labels:
        tp = sum(a == c and p == c for a,p in zip(y,pred))
        fp = sum(a != c and p == c for a,p in zip(y,pred))
        fn = sum(a == c and p != c for a,p in zip(y,pred))
        precision = tp/(tp+fp) if tp+fp else 0.0
        recall = tp/(tp+fn) if tp+fn else 0.0
        f1 = 2*precision*recall/(precision+recall) if precision+recall else 0.0
        per[c] = {"actual": int(sum(a==c for a in y)), "predicted": int(sum(p==c for p in pred)),
                  "correct": int(tp), "precision": precision, "recall": recall, "f1": f1}
    return {
        "n": int(len(y)), "accuracy": float(np.mean(np.asarray(y)==np.asarray(pred))),
        "macro_f1": float(np.mean([v["f1"] for v in per.values()])),
        "brier": float(np.mean(np.sum((arr-onehot)**2, axis=1))),
        "log_loss": float(-np.mean([math.log(max(EPS, arr[i, idx[v]])) for i, v in enumerate(y)])),
        "actual_counts": {c:int(sum(v==c for v in y)) for c in labels},
        "predicted_counts": {c:int(pred.count(c)) for c in labels},
        "per_class": per,
        "confusion_matrix_labels_"+"_".join(str(v) for v in labels): confusion_matrix(y,pred,labels=labels).tolist(),
    }


def fit_pipe(c, class_weight=None):
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=c, max_iter=4000, solver="lbfgs", class_weight=class_weight)),
    ])


def ordered_probs(model, X, labels):
    raw = model.predict_proba(X)
    classes = list(model.named_steps["model"].classes_)
    out = np.zeros((len(X), len(labels)), dtype=float)
    for j, label in enumerate(labels):
        if label in classes:
            out[:,j] = raw[:,classes.index(label)]
        else:
            out[:,j] = EPS
    out = np.clip(out, EPS, None)
    return out/out.sum(axis=1, keepdims=True)


def v3_draw_features(base, extra):
    # Probability/score distribution plus recent form/draw features; all are pre-match.
    return np.concatenate([base, extra], axis=1)


def select_c(Xtr, ytr, Xcal, ycal, labels):
    trials=[]
    for c in C_GRID:
        model=fit_pipe(c)
        model.fit(Xtr,ytr)
        p=ordered_probs(model,Xcal,labels)
        met=metric_block(list(ycal),p,labels)
        trials.append({"C":c,"calibration":met})
    chosen=sorted(trials,key=lambda t:(t["calibration"]["log_loss"],-t["calibration"]["accuracy"],t["C"]))[0]
    return chosen, trials


def main():
    if not DB.exists() or not MAP.exists():
        raise FileNotFoundError("Frozen database/mapping file missing.")
    con=sqlite3.connect(str(DB))
    cm.HIST_LIMIT=30
    with MAP.open(encoding="utf-8-sig",newline="") as f:
        mapping=list(csv.DictReader(f))
    if len(mapping)!=500 or len({x["match_id"] for x in mapping})!=500:
        raise ValueError("Expected 500 unique frozen match IDs.")
    if any(x.get("mapping_status")!="unique" for x in mapping):
        raise ValueError("Mapping must be unique for all 500 matches.")
    db={r[0]:r for r in con.execute("""
        SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
    """)}
    if any(x["match_id"] not in db for x in mapping):
        raise ValueError("Unresolved match in frozen mapping.")
    league={x["match_id"]:(x.get("league") or x.get("competition_code") or "UNKNOWN") for x in mapping}
    rows=sorted([db[x["match_id"]] for x in mapping],key=lambda r:(dt(r[1]),r[0]))
    if len(rows)!=500:
        raise ValueError("Resolved sample is not 500.")
    train,cal,hold=rows[:300],rows[300:400],rows[400:]
    y=[actual(r) for r in rows]
    yb=[bucket(r) for r in rows]
    hda_base=[]; bucket_base=[]; diag=[]; feats_hda=[]; feats_bucket=[]; draw_feats=[]
    for r in rows:
        lh,la=cm.model_lambdas(con,"v4",r) # intentionally no extra away-lambda suppression
        m=cm.matrix(float(lh),float(la),True)
        ph,pb,cells=probs_from_matrix(m)
        h5=recent_stats(con,r[2],r[1],5); a5=recent_stats(con,r[3],r[1],5)
        h10=recent_stats(con,r[2],r[1],10); a10=recent_stats(con,r[3],r[1],10)
        cutoff=(dt(r[1])-timedelta(hours=12)).isoformat(timespec="seconds")
        sh=float(cm.strength(con,r[2],cutoff)); sa=float(cm.strength(con,r[3],cutoff))
        total=float(lh+la); signed=float(lh-la); gap=abs(signed)
        base_probs=np.array([ph["H"],ph["D"],ph["A"]],float)
        bucket_probs=np.array([pb[k] for k in BUCKETS],float)
        extra=np.array([
            math.log(max(EPS,total)), signed, gap, float(lh), float(la),
            sh,sa,sh-sa, *cells,
            *h5,*a5,*h10,*a10
        ],float)
        hda_features=np.concatenate([np.log(np.clip(base_probs,EPS,1)),extra])
        bucket_features=np.concatenate([np.log(np.clip(bucket_probs,EPS,1)),extra])
        draw_features=np.array([
            math.log(max(EPS,total)),gap,min(lh,la),max(lh,la),ph["H"],ph["D"],ph["A"],
            *cells, (h5[0]+a5[0])/2,abs(h5[0]-a5[0]),
            (h10[0]+a10[0])/2,abs(h10[0]-a10[0]),
            (h5[1]+a5[1])/2,(h5[2]+a5[2])/2,abs(h5[3]-a5[3]),
            (h10[1]+a10[1])/2,(h10[2]+a10[2])/2,
            math.log(max(EPS,ph["H"])/max(EPS,ph["A"])),
            sh,sa,sh-sa
        ],float)
        hda_base.append(base_probs); bucket_base.append(bucket_probs)
        feats_hda.append(hda_features); feats_bucket.append(bucket_features); draw_feats.append(draw_features)
        diag.append({"match_id":r[0],"kickoff":r[1],"league":league[r[0]],"home":r[2],"away":r[3],
                     "actual":actual(r),"actual_score":f"{r[4]}-{r[5]}","actual_goal_diff_bucket":bucket(r),
                     "lambda_home":float(lh),"lambda_away":float(la),"lambda_total":total,"lambda_gap_abs":gap,
                     "base_p_home":ph["H"],"base_p_draw":ph["D"],"base_p_away":ph["A"],
                     **{f"base_bucket_p_{k}":pb[k] for k in BUCKETS},
                     "split":"train" if len(diag)<300 else "calibration" if len(diag)<400 else "blind_holdout"})
    Xh=np.asarray(feats_hda); Xb=np.asarray(feats_bucket); Xd=np.asarray(draw_feats)
    yarr=np.asarray(y); ybarr=np.asarray(yb)
    tr=np.arange(0,300); ca=np.arange(300,400); ho=np.arange(400,500)

    # A. Fix goal-difference calibration using richer pre-match features.
    bucket_choice,bucket_trials=select_c(Xb[tr],ybarr[tr],Xb[ca],ybarr[ca],BUCKETS)
    bucket_model=fit_pipe(bucket_choice["C"])
    bucket_model.fit(Xb[:400],ybarr[:400])
    bucket_hold=ordered_probs(bucket_model,Xb[ho],BUCKETS)
    bucket_raw_hold=np.asarray(bucket_base[400:])

    # B. Direct three-class H/D/A classifier, hyperparameter selected only on calibration.
    hda_choice,hda_trials=select_c(Xh[tr],yarr[tr],Xh[ca],yarr[ca],HDA)
    hda_model=fit_pipe(hda_choice["C"])
    hda_model.fit(Xh[:400],yarr[:400])
    direct_hold=ordered_probs(hda_model,Xh[ho],HDA)

    # C. Existing V3 two-stage draw model, same features and same 300/100/100 split.
    draw_choice,draw_trials=select_c(Xd[tr],np.asarray([1 if z=="D" else 0 for z in y])[tr],
                                     Xd[ca],np.asarray([1 if z=="D" else 0 for z in y])[ca],[0,1])
    # use binary class labels 0=not draw, 1=draw, then threshold chosen on calibration macro-F1.
    draw_model=fit_pipe(draw_choice["C"])
    draw_model.fit(Xd[tr],np.asarray([1 if z=="D" else 0 for z in y])[tr])
    dcal=ordered_probs(draw_model,Xd[ca],[0,1])[:,1]
    v3_threshold_candidates=[]
    for th in np.arange(0.10,0.601,0.01):
        pred=[]
        for i,pd in enumerate(dcal):
            ph,ppd,pa=hda_base[300+i]
            pred.append("D" if pd>=th else "H" if ph>=pa else "A")
        met=metric_block(list(yarr[ca]),np.array([[1-EPS,EPS,EPS] if p=="H" else [EPS,1-EPS,EPS] if p=="D" else [EPS,EPS,1-EPS] for p in pred]),HDA)
        draw=v3_threshold_candidates.append({"threshold":round(float(th),2),"metrics":met})
    best_threshold=sorted(v3_threshold_candidates,key=lambda t:(-t["metrics"]["macro_f1"],-t["metrics"]["accuracy"],-t["metrics"]["per_class"]["D"]["recall"],abs(t["metrics"]["per_class"]["D"]["predicted"]-t["metrics"]["per_class"]["D"]["actual"])))[0]["threshold"]
    draw_model=fit_pipe(draw_choice["C"])
    draw_model.fit(Xd[:400],np.asarray([1 if z=="D" else 0 for z in y])[tr.tolist()+ca.tolist()])
    dhold=ordered_probs(draw_model,Xd[ho],[0,1])[:,1]
    v3_hold=[]
    for i,pd in enumerate(dhold):
        ph,ppd,pa=hda_base[400+i]
        # Construct a coherent probability vector with the learned draw probability and base H:A ratio.
        ha=max(EPS,ph+pa)
        v3_hold.append([(1-pd)*ph/ha,pd,(1-pd)*pa/ha])
    v3_hold=np.asarray(v3_hold)

    base_hold=np.asarray(hda_base[400:])
    result={
        "experiment":"V4 goal-difference calibrator V2 + direct H/D/A comparison",
        "status":"EXPERIMENT_ONLY_NOT_PRODUCTION",
        "sample_gate":{"rows":500,"unique_match_id":len({r[0] for r in rows}),"mapping_unique":True},
        "split":{"train":300,"calibration":100,"blind_holdout":100,"train_end":rows[299][1],
                 "calibration_start":rows[300][1],"calibration_end":rows[399][1],"holdout_start":rows[400][1],"holdout_end":rows[499][1]},
        "lambda_policy":"Raw V4 lambdas only; no additional away-lambda downshift applied.",
        "goal_difference_calibrator":{
            "features":["base log bucket probabilities","home/away/total lambda","signed and absolute lambda gap","pre-match strength","0-0/1-1/2-2 cells","recent-5/10 form and draw rates"],
            "candidate_C":bucket_trials,"selected_C":bucket_choice["C"],
            "holdout_raw":metric_block(list(ybarr[ho]),bucket_raw_hold,BUCKETS),
            "holdout_calibrated":metric_block(list(ybarr[ho]),bucket_hold,BUCKETS),
            "holdout_delta_brier":float(metric_block(list(ybarr[ho]),bucket_hold,BUCKETS)["brier"]-metric_block(list(ybarr[ho]),bucket_raw_hold,BUCKETS)["brier"]),
            "holdout_delta_log_loss":float(metric_block(list(ybarr[ho]),bucket_hold,BUCKETS)["log_loss"]-metric_block(list(ybarr[ho]),bucket_raw_hold,BUCKETS)["log_loss"])
        },
        "same_holdout_hda_comparison":{
            "V4_raw_poisson":metric_block(list(yarr[ho]),base_hold,HDA),
            "V3_two_stage_draw":metric_block(list(yarr[ho]),v3_hold,HDA),
            "direct_three_class":metric_block(list(yarr[ho]),direct_hold,HDA),
            "direct_classifier_candidate_C":hda_trials,"direct_classifier_selected_C":hda_choice["C"],
            "V3_draw_model_selected_C":draw_choice["C"],"V3_draw_threshold_selected_on_calibration_only":best_threshold,
            "V3_draw_threshold_candidates":v3_threshold_candidates
        },
        "holdout_error_rows":[],
        "promotion_gate":"No promotion unless direct H/D/A improves accuracy and probability scores without materially worsening draw precision/recall; bucket calibrator must improve Brier/LogLoss and stop collapsing to a single bucket."
    }
    base_pred=[HDA[i] for i in base_hold.argmax(axis=1)]
    direct_pred=[HDA[i] for i in direct_hold.argmax(axis=1)]
    v3_pred=[HDA[i] for i in v3_hold.argmax(axis=1)]
    bucket_raw_pred=[BUCKETS[i] for i in bucket_raw_hold.argmax(axis=1)]
    bucket_cal_pred=[BUCKETS[i] for i in bucket_hold.argmax(axis=1)]
    out=[]
    for i,r in enumerate(rows):
        z=dict(diag[i])
        z.update({"base_hda_pred":HDA[int(np.argmax(hda_base[i]))],
                  "direct_hda_pred":direct_pred[i-400] if i>=400 else "",
                  "v3_two_stage_pred":v3_pred[i-400] if i>=400 else "",
                  "raw_bucket_pred":BUCKETS[int(np.argmax(bucket_base[i]))],
                  "calibrated_bucket_pred":bucket_cal_pred[i-400] if i>=400 else "",
                  "split":diag[i]["split"]})
        if i>=400:
            z.update({"direct_p_H":float(direct_hold[i-400,0]),"direct_p_D":float(direct_hold[i-400,1]),"direct_p_A":float(direct_hold[i-400,2]),
                      "v3_p_H":float(v3_hold[i-400,0]),"v3_p_D":float(v3_hold[i-400,1]),"v3_p_A":float(v3_hold[i-400,2]),
                      **{f"raw_bucket_p_{k}":float(bucket_raw_hold[i-400,j]) for j,k in enumerate(BUCKETS)},
                      **{f"cal_bucket_p_{k}":float(bucket_hold[i-400,j]) for j,k in enumerate(BUCKETS)}})
            if z["actual"]=="D" and z["base_hda_pred"]!="D":
                z["error_type"]="actual_draw_missed_by_base"
            elif z["actual"]!="D" and z["base_hda_pred"]=="D":
                z["error_type"]="false_draw_by_base"
            else:
                z["error_type"]=""
            result["holdout_error_rows"].append(z)
        out.append(z)
    with OUT_CSV.open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(out[0].keys()))
        writer.writeheader();writer.writerows(out)
    OUT_JSON.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="holdout_error_rows"},ensure_ascii=False,indent=2))
    print("HOLDOUT_ERROR_COUNTS",json.dumps({
        "actual_draw_missed_by_base":sum(r["actual"]=="D" and r["base_hda_pred"]!="D" for r in result["holdout_error_rows"]),
        "false_draw_by_base":sum(r["actual"]!="D" and r["base_hda_pred"]=="D" for r in result["holdout_error_rows"]),
        "direct_actual_draw_hits":sum(r["actual"]=="D" and r["direct_hda_pred"]=="D" for r in result["holdout_error_rows"]),
        "v3_actual_draw_hits":sum(r["actual"]=="D" and r["v3_two_stage_pred"]=="D" for r in result["holdout_error_rows"])
    },ensure_ascii=False))
    con.close()


if __name__=="__main__":
    main()
