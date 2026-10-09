#!/usr/bin/env python3
"""Experiment-only direct H/D/A classifier on the frozen 500-match sample.
Uses V4/V5 pre-match lambda and score-grid probabilities plus team form computed
strictly before each match's T-12h cutoff. Production model is not modified.
"""
import csv, json, math, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_pipeline"))
import v4_v5_joint_engine_validation_500 as base

OUT_JSON = ROOT / "model_validation/v5_1_direct_outcome_classifier_500_summary.json"
OUT_CSV = ROOT / "model_validation/v5_1_direct_outcome_classifier_500_predictions.csv"
OUT_REPORT = ROOT / "model_validation/V5_1_DIRECT_OUTCOME_CLASSIFIER_EXPERIMENT_2026-10-09.md"
CLASSES = ("H", "D", "A")
LEAGUES = ("EPL", "ESP", "GER", "ITA", "FRA", "JPN", "KOR")
EPS = 1e-12


def team_form(con, team, cutoff, n):
    rows = con.execute(
        """SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
           FROM matches m JOIN results r ON r.match_id=m.match_id
           WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
             AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
           ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?""",
        (cutoff, team, team, n),
    ).fetchall()
    if not rows:
        return [0.0] * 6
    pts, gf, ga, wins, draws = [], [], [], 0, 0
    for _, home, away, fh, fa in rows:
        scored, conceded = (float(fh), float(fa)) if home == team else (float(fa), float(fh))
        gf.append(scored); ga.append(conceded)
        if scored > conceded:
            pts.append(3); wins += 1
        elif scored == conceded:
            pts.append(1); draws += 1
        else:
            pts.append(0)
    m = len(rows)
    return [sum(pts)/(3.0*m), sum(gf)/m, sum(ga)/m,
            sum(gf[i]-ga[i] for i in range(m))/m, wins/m, draws/m]


def features_for(con, x):
    mid, kickoff, home, away, fh, fa = x["match"]
    cutoff = (datetime.fromisoformat(kickoff[:19]) - timedelta(hours=12)).isoformat(timespec="seconds")
    v4h, v4a = x["v4"]; v5h, v5a = x["v5"]
    blend_h = math.exp(.65*math.log(max(EPS,v4h)) + .35*math.log(max(EPS,v5h)))
    blend_a = math.exp(.65*math.log(max(EPS,v4a)) + .35*math.log(max(EPS,v5a)))
    vals = [math.log(max(EPS,v4h)), math.log(max(EPS,v4a)),
            math.log(max(EPS,v5h)), math.log(max(EPS,v5a)),
            math.log(max(EPS,blend_h)), math.log(max(EPS,blend_a)),
            v4h-v4a, v5h-v5a, blend_h-blend_a, v4h+v4a, v5h+v5a, blend_h+blend_a]
    for matrix in (x["m4"], x["m5"], base.matrix(blend_h,blend_a)):
        p = base.matrix_probs(matrix); vals.extend([p["H"],p["D"],p["A"]])
    hf5, hf10 = team_form(con,home,cutoff,5), team_form(con,home,cutoff,10)
    af5, af10 = team_form(con,away,cutoff,5), team_form(con,away,cutoff,10)
    vals.extend(hf5); vals.extend(hf10); vals.extend(af5); vals.extend(af10)
    vals.extend([hf5[i]-af5[i] for i in range(6)])
    vals.extend([hf10[i]-af10[i] for i in range(6)])
    league = (x["mapping"].get("competition_code") or x["mapping"].get("league") or "").upper()
    vals.extend([1.0 if league == code else 0.0 for code in LEAGUES])
    return vals


def aligned_probabilities(model, X):
    raw = model.predict_proba(X)
    out = []
    for row in raw:
        d = {str(c): float(p) for c,p in zip(model.classes_,row)}
        out.append(np.array([d.get(c,0.0) for c in CLASSES],dtype=float))
    return np.vstack(out)


def temp_scale(probs, temperature):
    logits = np.log(np.clip(probs,EPS,1.0))/temperature
    logits -= logits.max(axis=1,keepdims=True)
    exp = np.exp(logits)
    return exp/exp.sum(axis=1,keepdims=True)


def rps_one(p, actual):
    y = [1.0 if actual == "A" else 0.0, 1.0 if actual in ("A","D") else 0.0]
    c = [p[2], p[2]+p[1]]
    return .5*sum((c[i]-y[i])**2 for i in range(2))


def metrics(probs, labels):
    pred = [CLASSES[i] for i in np.argmax(probs,axis=1)]
    n=len(labels)
    tp=sum(p=="D" and y=="D" for p,y in zip(pred,labels))
    fp=sum(p=="D" and y!="D" for p,y in zip(pred,labels))
    fn=sum(p!="D" and y=="D" for p,y in zip(pred,labels))
    brier=ll=rps=0.0
    for p,y in zip(probs,labels):
        target=np.array([1.0 if c==y else 0.0 for c in CLASSES])
        brier += float(np.sum((p-target)**2))
        ll -= math.log(max(EPS,float(p[CLASSES.index(y)])))
        rps += rps_one(p,y)
    return {"n":n,"correct":int(sum(p==y for p,y in zip(pred,labels))),
            "accuracy":sum(p==y for p,y in zip(pred,labels))/n if n else None,
            "draw_tp":int(tp),"draw_fp":int(fp),"draw_fn":int(fn),
            "draw_recall":tp/(tp+fn) if tp+fn else 0.0,
            "draw_precision":tp/(tp+fp) if tp+fp else 0.0,
            "brier":brier/n if n else None,"logloss":ll/n if n else None,"rps":rps/n if n else None}


def probs_from_base(rows, arm):
    out=[]
    for x in rows:
        if arm=="v4": matrix=x["m4"]
        elif arm=="v5": matrix=x["m5"]
        else:
            lh=math.exp(.65*math.log(max(EPS,x["v4"][0]))+.35*math.log(max(EPS,x["v5"][0])))
            la=math.exp(.65*math.log(max(EPS,x["v4"][1]))+.35*math.log(max(EPS,x["v5"][1])))
            matrix=base.matrix(lh,la)
        p=base.matrix_probs(matrix); out.append([p["H"],p["D"],p["A"]])
    return np.array(out,dtype=float)


def main():
    rows=base.get_rows()
    rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    if len(rows)!=500: raise RuntimeError(f"Expected frozen 500 sample, got {len(rows)}")
    n=len(rows); train_end,cal_end=int(n*.60),int(n*.80)
    train,cal,test=rows[:train_end],rows[train_end:cal_end],rows[cal_end:]
    con=sqlite3.connect(base.DB)
    try: X=np.asarray([features_for(con,x) for x in rows],dtype=float)
    finally: con.close()
    y=np.asarray([x["actual"] for x in rows],dtype=str)
    X_train,X_cal,X_test=X[:train_end],X[train_end:cal_end],X[cal_end:]
    y_train,y_cal,y_test=y[:train_end],y[train_end:cal_end],y[cal_end:]
    model=make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=3000,solver="lbfgs",random_state=51))
    model.fit(X_train,y_train)
    p_train=aligned_probabilities(model,X_train)
    p_cal=aligned_probabilities(model,X_cal)
    p_test=aligned_probabilities(model,X_test)
    # Fit one temperature on calibration only; final holdout is never used for selection.
    candidates=np.linspace(.50,3.00,251)
    temp_scores=[(metrics(temp_scale(p_cal,float(t)),y_cal)["logloss"],float(t)) for t in candidates]
    calibration_logloss,temperature=min(temp_scores,key=lambda z:(z[0],abs(z[1]-1.0)))
    p_cal_scaled=temp_scale(p_cal,temperature)
    p_test_scaled=temp_scale(p_test,temperature)
    splits={"train":(train,y_train,p_train),"calibration":(cal,y_cal,p_cal),"final_holdout":(test,y_test,p_test)}
    summary={"status":"COMPLETED_EXPERIMENT_ONLY","model":"multinomial LogisticRegression C=0.3, StandardScaler; T-12h pre-match features; no market odds features",
      "sample_n":n,"split":{"train_n":len(train),"calibration_n":len(cal),"final_holdout_n":len(test)},
      "feature_count":int(X.shape[1]),
      "feature_groups":["V4/V5/blended lambdas","V4/V5/blended H/D/A matrix probabilities","home/away recent 5 and 10 match form","home-away form differentials","league indicators"],
      "cutoff_rule":"all history and features are restricted to kickoff earlier than each match T-12h cutoff",
      "temperature_scaling":{"selected_on_calibration_logloss_only":temperature,"calibration_logloss_after_scaling":calibration_logloss},
      "arms":{},"decision":{"production_status":"UNCHANGED; EXPERIMENT_ONLY"}}
    for arm in ("v4","v5","joint"):
        p={k:probs_from_base(v[0],arm) for k,v in splits.items()}
        summary["arms"][arm]={k:metrics(p[k],v[1]) for k,v in splits.items()}
    summary["arms"]["direct_lr_raw"]={k:metrics(v[2],v[1]) for k,v in splits.items()}
    summary["arms"]["direct_lr_temperature_scaled"]={
        "train":metrics(temp_scale(p_train,temperature),y_train),
        "calibration":metrics(p_cal_scaled,y_cal),
        "final_holdout":metrics(p_test_scaled,y_test)}
    baseline=summary["arms"]["joint"]["final_holdout"]
    candidate=summary["arms"]["direct_lr_temperature_scaled"]["final_holdout"]
    summary["decision"].update({
      "accuracy_delta_vs_joint":candidate["accuracy"]-baseline["accuracy"],
      "brier_delta_vs_joint":candidate["brier"]-baseline["brier"],
      "logloss_delta_vs_joint":candidate["logloss"]-baseline["logloss"],
      "rps_delta_vs_joint":candidate["rps"]-baseline["rps"],
      "beats_joint_accuracy":candidate["accuracy"]>baseline["accuracy"],
      "beats_joint_brier":candidate["brier"]<baseline["brier"],
      "beats_joint_logloss":candidate["logloss"]<baseline["logloss"],
      "draw_recall_improved_vs_joint":candidate["draw_recall"]>baseline["draw_recall"],
      "promotion_gate_passed":candidate["accuracy"]>baseline["accuracy"] and candidate["brier"]<baseline["brier"] and candidate["logloss"]<baseline["logloss"] and candidate["draw_recall"]>baseline["draw_recall"]})
    with OUT_JSON.open("w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        fields=["match_id","kickoff","home","away","actual_score","actual_90","split",
          "joint_pred_90","joint_p_home","joint_p_draw","joint_p_away",
          "direct_lr_pred_raw","direct_lr_p_home_raw","direct_lr_p_draw_raw","direct_lr_p_away_raw",
          "direct_lr_pred_calibrated","direct_lr_p_home_calibrated","direct_lr_p_draw_calibrated","direct_lr_p_away_calibrated"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        all_raw=aligned_probabilities(model,X)
        all_cal=temp_scale(all_raw,temperature)
        for i,x in enumerate(rows):
            split="train" if i<train_end else "calibration" if i<cal_end else "final_holdout"
            pj=probs_from_base([x],"joint")[0]
            raw,calibrated=all_raw[i],all_cal[i]
            label=lambda p: CLASSES[int(np.argmax(p))]
            w.writerow({"match_id":x["match"][0],"kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
              "actual_score":f"{x['match'][4]}-{x['match'][5]}","actual_90":x["actual"],"split":split,
              "joint_pred_90":label(pj),"joint_p_home":pj[0],"joint_p_draw":pj[1],"joint_p_away":pj[2],
              "direct_lr_pred_raw":label(raw),"direct_lr_p_home_raw":raw[0],"direct_lr_p_draw_raw":raw[1],"direct_lr_p_away_raw":raw[2],
              "direct_lr_pred_calibrated":label(calibrated),"direct_lr_p_home_calibrated":calibrated[0],
              "direct_lr_p_draw_calibrated":calibrated[1],"direct_lr_p_away_calibrated":calibrated[2]})
    report=["# V5.1 独立胜平负分类器实验（实验版）","",
      "- 状态：COMPLETED_EXPERIMENT_ONLY；生产模型保持不变。",
      "- 样本：固定 500 场，按时间排序 300 训练 / 100 校准 / 100 最终盲测。",
      "- 模型：多项逻辑回归，固定 C=0.3；标准化器只在训练集拟合；温度缩放参数只在校准集选择。",
      "- 特征：V4/V5/联合模型赛前 λ 和比分矩阵概率、赛前近 5/10 场球队状态、主客状态差、联赛指示变量。",
      "- 赛前截断：每场仅使用开赛前 12 小时之前的历史数据；未使用最终比分作为输入特征。",
      "- 资金流/竞彩盘口未作为特征，避免将未核实数据混入模型。","",
      "## 最终 100 场盲测","","| 模型 | 准确率 | 平局召回率 | Brier（越低越好） | Log Loss（越低越好） | RPS（越低越好） |",
      "|---|---:|---:|---:|---:|---:|"]
    for arm,title in (("v4","V4"),("v5","重建版 V5"),("joint","V4/V5 联合基线"),("direct_lr_raw","独立分类器（原始概率）"),("direct_lr_temperature_scaled","独立分类器（温度校准）")):
        m=summary["arms"][arm]["final_holdout"]
        report.append(f"| {title} | {m['accuracy']:.1%} | {m['draw_recall']:.1%} | {m['brier']:.4f} | {m['logloss']:.4f} | {m['rps']:.4f} |")
    report.extend(["","## 验收结论","",
      f"- 相对联合基线准确率变化：{summary['decision']['accuracy_delta_vs_joint']:+.1%}。",
      f"- 相对联合基线 Brier 变化：{summary['decision']['brier_delta_vs_joint']:+.4f}。",
      f"- 相对联合基线 Log Loss 变化：{summary['decision']['logloss_delta_vs_joint']:+.4f}。",
      f"- 相对联合基线 RPS 变化：{summary['decision']['rps_delta_vs_joint']:+.4f}。",
      f"- 验收门槛是否全部通过：{summary['decision']['promotion_gate_passed']}。",
      "- 不自动修改生产模型；若未同时改善准确率、Brier、Log Loss 和平局召回率，则保留为实验方案。",
      "","## 输出文件","","- v5_1_direct_outcome_classifier_500_summary.json","- v5_1_direct_outcome_classifier_500_predictions.csv"])
    OUT_REPORT.write_text("\n".join(report)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
