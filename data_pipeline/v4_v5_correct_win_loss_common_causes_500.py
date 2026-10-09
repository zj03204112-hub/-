#!/usr/bin/env python3
"""Explain correct vs incorrect H/A calls and diagnose missed draws on frozen 500.
Descriptive, leakage-aware T-12h features only; no model fitting or production changes.
"""
import csv, json, math, sqlite3, statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import v4_v5_joint_engine_validation_500 as base
import compare_models as cm
OUT_CSV=ROOT/"model_validation/v4_v5_correct_win_loss_common_causes_500.csv"
OUT_JSON=ROOT/"model_validation/v4_v5_correct_win_loss_common_causes_500.json"
W=0.65

def mean(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return round(sum(xs)/len(xs),4) if xs else None
def median(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return round(statistics.median(xs),4) if xs else None
def outcome(h,a): return "H" if h>a else "D" if h==a else "A"
def matrix_probs(m): return base.matrix_probs(m)
def lambda_blend(x):
    lh=math.exp(W*math.log(max(1e-9,x["v4"][0]))+(1-W)*math.log(max(1e-9,x["v5"][0])))
    la=math.exp(W*math.log(max(1e-9,x["v4"][1]))+(1-W)*math.log(max(1e-9,x["v5"][1])))
    return lh,la

def prior_features(con,team,cutoff,venue=None):
    venue_clause = " AND m.home_team=?" if venue=="home" else " AND m.away_team=?" if venue=="away" else ""
    params=(cutoff,team,team,team) if venue_clause else (cutoff,team,team)
    rows=con.execute("""SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
      AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL""" + venue_clause +
      " ORDER BY m.kickoff DESC,m.match_id DESC LIMIT 5",params).fetchall()
    pts=[]; gf=[]; ga=[]; gd=[]; dates=[]
    for ko,h,a,fh,fa in rows:
        is_home=(h==team)
        x,y=(int(fh),int(fa)) if is_home else (int(fa),int(fh))
        pts.append(3 if x>y else 1 if x==y else 0)
        gf.append(x);ga.append(y);gd.append(x-y);dates.append(ko)
    allrows=con.execute("""SELECT m.kickoff,m.home_team,m.away_team
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
      AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
      ORDER BY m.kickoff DESC,m.match_id DESC LIMIT 1""",(cutoff,team,team)).fetchone()
    rest=None
    if allrows:
        try: rest=(datetime.fromisoformat(cutoff[:19])-datetime.fromisoformat(allrows[0][:19])).total_seconds()/86400
        except Exception: pass
    return {"form5_points":sum(pts) if pts else None,"form5_points_per_match":mean(pts),
      "form5_gf":mean(gf),"form5_ga":mean(ga),"form5_gd":mean(gd),
      "rest_days":round(rest,2) if rest is not None else None,"form5_n":len(pts)}

def safe_table_columns(con,table):
    try: return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except Exception: return set()

def aux_value(con,table,match_id,side,fields):
    cols=safe_table_columns(con,table)
    if not cols or "match_id" not in cols or "team_side" not in cols: return {}
    available=[f for f in fields if f in cols]
    if not available: return {}
    q="SELECT "+",".join(available)+f" FROM {table} WHERE match_id=? AND team_side=? LIMIT 1"
    row=con.execute(q,(match_id,side)).fetchone()
    return dict(zip(available,row)) if row else {}

def decile_group(v):
    if v is None:return "unknown"
    if v<0.05:return "0.00-0.05"
    if v<0.10:return "0.05-0.10"
    if v<0.20:return "0.10-0.20"
    if v<0.35:return "0.20-0.35"
    if v<0.50:return "0.35-0.50"
    return "0.50+"

def summarize(rows,group_key):
    out={}
    for key in sorted(set(r[group_key] for r in rows)):
        a=[r for r in rows if r[group_key]==key]
        out[key]={"n":len(a),"accuracy":mean([r["correct"] for r in a]),
          "mean_lambda_gap_abs":mean([abs(r["lambda_gap"]) for r in a]),
          "median_lambda_gap_abs":median([abs(r["lambda_gap"]) for r in a]),
          "mean_lambda_total":mean([r["lambda_total"] for r in a]),
          "mean_probability_margin":mean([r["probability_margin"] for r in a]),
          "mean_strength_gap_abs":mean([abs(r["strength_gap"]) for r in a]),
          "mean_form5_points_gap":mean([r["home_form5_points"]-r["away_form5_points"] for r in a if r["home_form5_points"] is not None and r["away_form5_points"] is not None]),
          "mean_rest_days_gap":mean([r["home_rest_days"]-r["away_rest_days"] for r in a if r["home_rest_days"] is not None and r["away_rest_days"] is not None])}
    return out

def main():
    data=base.get_rows()
    data.sort(key=lambda x:(x["match"][1],x["match"][0]))
    con=sqlite3.connect(base.DB)
    rows=[]
    for x in data:
        mid,ko,home,away,fh,fa=x["match"]
        cutoff=(datetime.fromisoformat(ko[:19])-timedelta(hours=12)).isoformat(timespec="seconds")
        lh,la=lambda_blend(x); p=matrix_probs(base.matrix(lh,la))
        pred=max(p,key=p.get); actual=x["actual"]
        strength_h=cm.real_strength_metrics(con,home,cutoff)
        strength_a=cm.real_strength_metrics(con,away,cutoff)
        form_h=prior_features(con,home,cutoff)
        form_a=prior_features(con,away,cutoff)
        h5=prior_features(con,home,cutoff,"home")
        a5=prior_features(con,away,cutoff,"away")
        lp_h=aux_value(con,"lineup_projection",mid,"home",["attack_delta","defense_delta"])
        lp_a=aux_value(con,"lineup_projection",mid,"away",["attack_delta","defense_delta"])
        si_h=aux_value(con,"schedule_intent",mid,"home",["motivation_adjustment"])
        si_a=aux_value(con,"schedule_intent",mid,"away",["motivation_adjustment"])
        probs=sorted(p.values(),reverse=True)
        cls=("correct_home_win" if actual=="H" and pred=="H" else
             "missed_home_win" if actual=="H" else
             "correct_away_win" if actual=="A" and pred=="A" else
             "missed_away_win" if actual=="A" else
             "draw_predicted_correctly" if pred=="D" else "draw_missed")
        rows.append({
          "match_id":mid,"kickoff":ko,"league":x["mapping"].get("league",""),
          "home":home,"away":away,"actual_score":f"{fh}-{fa}","actual_90":actual,
          "pred_90":pred,"classification":cls,"correct":int(pred==actual),
          "p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),
          "probability_margin":round(probs[0]-probs[1],6),
          "lambda_home":round(lh,4),"lambda_away":round(la,4),
          "lambda_gap":round(lh-la,4),"lambda_total":round(lh+la,4),
          "strength_home":strength_h["score"],"strength_away":strength_a["score"],
          "strength_gap":round(strength_h["score"]-strength_a["score"],4),
          "home_form5_points":form_h["form5_points"],"away_form5_points":form_a["form5_points"],
          "home_form5_gd":form_h["form5_gd"],"away_form5_gd":form_a["form5_gd"],
          "home_rest_days":form_h["rest_days"],"away_rest_days":form_a["rest_days"],
          "home_home_form5_points":h5["form5_points"],"away_away_form5_points":a5["form5_points"],
          "home_lineup_attack_delta":lp_h.get("attack_delta"),"away_lineup_attack_delta":lp_a.get("attack_delta"),
          "home_lineup_defense_delta":lp_h.get("defense_delta"),"away_lineup_defense_delta":lp_a.get("defense_delta"),
          "home_motivation_adjustment":si_h.get("motivation_adjustment"),
          "away_motivation_adjustment":si_a.get("motivation_adjustment"),
          "draw_probability_band":decile_group(p["D"]),
          "probability_margin_band":decile_group(probs[0]-probs[1])
        })
    con.close()
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(rows[0].keys()));wr.writeheader();wr.writerows(rows)
    classes={}
    for key in sorted(set(r["classification"] for r in rows)):
        rr=[r for r in rows if r["classification"]==key]
        classes[key]={"n":len(rr),"accuracy":mean([r["correct"] for r in rr]),
          "actual_outcomes":dict(Counter(r["actual_90"] for r in rr)),
          "predicted_outcomes":dict(Counter(r["pred_90"] for r in rr)),
          "mean_p_home":mean([r["p_home"] for r in rr]),"mean_p_draw":mean([r["p_draw"] for r in rr]),
          "mean_p_away":mean([r["p_away"] for r in rr]),
          "mean_lambda_gap":mean([r["lambda_gap"] for r in rr]),
          "mean_lambda_total":mean([r["lambda_total"] for r in rr]),
          "mean_probability_margin":mean([r["probability_margin"] for r in rr]),
          "mean_strength_gap":mean([r["strength_gap"] for r in rr]),
          "mean_home_form5_points":mean([r["home_form5_points"] for r in rr]),
          "mean_away_form5_points":mean([r["away_form5_points"] for r in rr]),
          "mean_home_rest_days":mean([r["home_rest_days"] for r in rr]),
          "mean_away_rest_days":mean([r["away_rest_days"] for r in rr])}
    # Actual result stratification separates correctly classified wins and losses from the errors.
    actual_strata={}
    for act in ("H","A","D"):
        rr=[r for r in rows if r["actual_90"]==act]
        actual_strata[act]={"n":len(rr),"correct_n":sum(r["correct"] for r in rr),
          "accuracy":mean([r["correct"] for r in rr]),
          "by_prediction":dict(Counter(r["pred_90"] for r in rr)),
          "by_lambda_gap_sign":dict(Counter("home_lambda_higher" if r["lambda_gap"]>0.10 else "away_lambda_higher" if r["lambda_gap"]<-0.10 else "lambda_near_equal" for r in rr))}
    # Correct H and A calls versus missed H and A outcomes: compare the same actual class.
    home_comparison=summarize([r for r in rows if r["actual_90"]=="H"],"classification")
    away_comparison=summarize([r for r in rows if r["actual_90"]=="A"],"classification")
    # League-level error pattern, plus λ and probability-margin bands.
    league_stats={}
    for lg in sorted(set(r["league"] for r in rows)):
        rr=[r for r in rows if r["league"]==lg]
        league_stats[lg]={"n":len(rr),"accuracy":mean([r["correct"] for r in rr]),
          "draws":sum(r["actual_90"]=="D" for r in rr),
          "draw_recall":(sum(r["actual_90"]=="D" and r["pred_90"]=="D" for r in rr)/sum(r["actual_90"]=="D" for r in rr)) if any(r["actual_90"]=="D" for r in rr) else None}
    summary={"status":"DIAGNOSTIC_ONLY_NO_MODEL_CHANGE","sample_n":len(rows),
      "split":"Full frozen 500 descriptive audit; use only for diagnosis, not parameter fitting",
      "prediction":"λ-level joint reconstruction with V4 share 0.65 / V5 reconstruction share 0.35",
      "feature_cutoff":"T-12h before kickoff for form, strength, lineup projection and schedule intent lookup",
      "overall":{"accuracy":mean([r["correct"] for r in rows]),"correct_n":sum(r["correct"] for r in rows),
        "actual_counts":dict(Counter(r["actual_90"] for r in rows)),"predicted_counts":dict(Counter(r["pred_90"] for r in rows)),
        "mean_p_draw":mean([r["p_draw"] for r in rows])},
      "classes":classes,"actual_outcome_strata":actual_strata,
      "correct_vs_missed_home_wins":home_comparison,"correct_vs_missed_away_wins":away_comparison,
      "league_stats":league_stats,
      "probability_margin_bands":summarize(rows,"probability_margin_band"),
      "lambda_gap_bands":{label:{"n":sum(1 for r in rows if fn(r["lambda_gap"])),"accuracy":mean([r["correct"] for r in rows if fn(r["lambda_gap"])]),"draws":sum(1 for r in rows if fn(r["lambda_gap"]) and r["actual_90"]=="D")} for label,fn in [("home_lambda_ahead_gt_0_35",lambda v:v>0.35),("home_lambda_ahead_0_10_to_0_35",lambda v:0.10<v<=0.35),("near_equal_abs_le_0_10",lambda v:abs(v)<=0.10),("away_lambda_ahead_0_10_to_0_35",lambda v:-0.35<=v< -0.10),("away_lambda_ahead_gt_0_35",lambda v:v< -0.35)]},
      "interpretation_guardrail":"Descriptive correlations are not causal proof. Lineup/schedule deltas may be missing; null means unavailable, not neutral in reality. No feature is added to production by this audit."}
    OUT_JSON.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
