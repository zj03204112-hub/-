"""Chronological shadow backtest for market-anchored lambda allocation.

Input is an immutable CSV of verified pre-kickoff market snapshots + final scores.
No missing odds are imputed. If no eligible rows exist, exits with BLOCKED status.
"""
from __future__ import annotations
import csv, json, math, os, sys
from datetime import datetime
from pathlib import Path
from collections import Counter
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.market_lambda_allocation import market_anchored_prediction, score_grid

INPUT = Path(os.environ.get("MARKET_BACKTEST_CSV", "data/market_lambda_backtest.csv"))
OUT = Path(os.environ.get("MARKET_BACKTEST_OUT", "model_validation/market_lambda_chronological_report.json"))
BINS = ["0-1", "2", "3-4", "5-6", "7+"]

def parse_dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))

def safe_log(p):
    return -math.log(max(1e-15, min(1.0, p)))

def brier_multiclass(probs, actual):
    return sum((probs[i] - (1.0 if i == actual else 0.0)) ** 2 for i in range(len(probs)))

def read_rows(path):
    if not path.exists():
        return [], [f"missing input file: {path}"]
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    required = {"match_id","kickoff_utc","snapshot_utc","snapshot_type","home_goals","away_goals",
                "home_odds","draw_odds","away_odds","ou_line","over_odds","under_odds",
                "baseline_lambda_home","baseline_lambda_away"}
    missing = required - (set(rows[0]) if rows else set())
    if missing:
        return [], [f"missing required columns: {','.join(sorted(missing))}"]
    eligible, reasons = [], []
    seen = set()
    for idx, r in enumerate(rows, start=2):
        try:
            if r["match_id"] in seen: raise ValueError("duplicate match_id")
            seen.add(r["match_id"])
            kickoff, snap = parse_dt(r["kickoff_utc"]), parse_dt(r["snapshot_utc"])
            if snap >= kickoff: raise ValueError("snapshot not strictly pre-kickoff")
            if r["snapshot_type"].lower() not in {"prematch","pre_match","赛前","赛前快照"}:
                raise ValueError("snapshot_type is not a recognized pre-match value")
            nums = ["home_goals","away_goals","home_odds","draw_odds","away_odds","ou_line","over_odds","under_odds","baseline_lambda_home","baseline_lambda_away"]
            for n in nums:
                if not math.isfinite(float(r[n])): raise ValueError(f"invalid numeric {n}")
            if float(r["home_goals"]) < 0 or float(r["away_goals"]) < 0: raise ValueError("negative goals")
            eligible.append(r)
        except Exception as e:
            reasons.append(f"row {idx} rejected: {e}")
    eligible.sort(key=lambda r: parse_dt(r["kickoff_utc"]))
    return eligible, reasons

def evaluate(rows):
    result = {"n": len(rows), "goal_bin_brier": None, "goal_bin_log_loss": None,
              "goal_bin_brier_baseline": None, "goal_bin_log_loss_baseline": None,
              "goal_tail": {}, "hda_brier": None, "hda_log_loss": None,
              "hda_brier_baseline": None, "hda_log_loss_baseline": None,
              "score1_hit_rate": None, "score2_hit_rate": None,
              "baseline_score1_hit_rate": None, "baseline_score2_hit_rate": None,
              "avg_expected_total_goals_mae": None}
    if not rows: return result
    cand_goal_briers=[]; cand_goal_ll=[]; base_goal_briers=[]; base_goal_ll=[]
    cand_hda_briers=[]; cand_hda_ll=[]; base_hda_briers=[]; base_hda_ll=[]
    cand_s1=cand_s2=base_s1=base_s2=0; total_mae=[]
    tail={"5-6":{"predicted":0.0,"observed":0},"7+":{"predicted":0.0,"observed":0}}
    for r in rows:
        hg, ag = int(float(r["home_goals"])), int(float(r["away_goals"]))
        total = hg + ag
        actual_bin = 0 if total <= 1 else 1 if total == 2 else 2 if total <= 4 else 3 if total <= 6 else 4
        actual_hda = 0 if hg > ag else 1 if hg == ag else 2
        pred = market_anchored_prediction(
            over_odds=float(r["over_odds"]), under_odds=float(r["under_odds"]), goal_line=float(r["ou_line"]),
            home_odds=float(r["home_odds"]), draw_odds=float(r["draw_odds"]), away_odds=float(r["away_odds"]))
        cp = [pred["distribution"]["goal_bins"][b] for b in BINS]
        base = score_grid(float(r["baseline_lambda_home"]), float(r["baseline_lambda_away"]))
        bp = [base["goal_bins"][b] for b in BINS]
        cand_goal_briers.append(brier_multiclass(cp, actual_bin)); cand_goal_ll.append(safe_log(cp[actual_bin]))
        base_goal_briers.append(brier_multiclass(bp, actual_bin)); base_goal_ll.append(safe_log(bp[actual_bin]))
        ch=[pred["distribution"]["p_home"],pred["distribution"]["p_draw"],pred["distribution"]["p_away"]]
        bh=[base["p_home"],base["p_draw"],base["p_away"]]
        cand_hda_briers.append(brier_multiclass(ch, actual_hda)); cand_hda_ll.append(safe_log(ch[actual_hda]))
        base_hda_briers.append(brier_multiclass(bh, actual_hda)); base_hda_ll.append(safe_log(bh[actual_hda]))
        s1=pred["distribution"]["top_scores"][0]; s2=pred["distribution"]["top_scores"][1]
        cand_s1 += int((s1["home"],s1["away"]) == (hg,ag))
        cand_s2 += int((s2["home"],s2["away"]) == (hg,ag))
        btop=base["top_scores"]
        base_s1 += int((btop[0]["home"],btop[0]["away"]) == (hg,ag))
        base_s2 += int((btop[1]["home"],btop[1]["away"]) == (hg,ag))
        total_mae.append(abs(pred["lambda_total"] - total))
        for b in ("5-6","7+"):
            tail[b]["predicted"] += pred["distribution"]["goal_bins"][b]
            tail[b]["observed"] += int((5 <= total <= 6) if b=="5-6" else total>=7)
    n=len(rows)
    result.update({
        "goal_bin_brier":sum(cand_goal_briers)/n,"goal_bin_log_loss":sum(cand_goal_ll)/n,
        "goal_bin_brier_baseline":sum(base_goal_briers)/n,"goal_bin_log_loss_baseline":sum(base_goal_ll)/n,
        "hda_brier":sum(cand_hda_briers)/n,"hda_log_loss":sum(cand_hda_ll)/n,
        "hda_brier_baseline":sum(base_hda_briers)/n,"hda_log_loss_baseline":sum(base_hda_ll)/n,
        "score1_hit_rate":cand_s1/n,"score2_hit_rate":cand_s2/n,
        "baseline_score1_hit_rate":base_s1/n,"baseline_score2_hit_rate":base_s2/n,
        "avg_expected_total_goals_mae":sum(total_mae)/n,
        "goal_tail":{b:{"predicted_mean_probability":v["predicted"]/n,
                        "observed_rate":v["observed"]/n,
                        "absolute_calibration_gap":abs(v["predicted"]/n-v["observed"]/n)}
                     for b,v in tail.items()}
    })
    return result

def main():
    rows, rejected = read_rows(INPUT)
    report={"status":"BLOCKED","input":str(INPUT),"eligible_rows":len(rows),
            "rejected_rows":len(rejected),"rejection_reasons":rejected[:100],
            "split":"chronological 70% development / 30% untouched later holdout",
            "leakage_guard":"snapshot_utc must be earlier than kickoff_utc; no odds imputation",
            "metrics":{}}
    if len(rows) < 10:
        report["blocker"]="Need at least 10 eligible matches with real pre-kickoff O/U + 1X2 odds, baseline lambdas, final scores, and UTC timestamps. No metric is reported on incomplete data."
    else:
        cut=max(1,min(len(rows)-1,int(len(rows)*0.7)))
        train, test=rows[:cut], rows[cut:]
        report["status"]="COMPLETED" if len(test)>=3 else "BLOCKED"
        report["development_n"]=len(train); report["holdout_n"]=len(test)
        report["development_metrics"]=evaluate(train)
        report["metrics"]=evaluate(test)
        if len(test)<3: report["blocker"]="Chronological holdout has fewer than 3 matches."
        else:
            m=report["metrics"]
            report["acceptance"]={
                "goal_bin_log_loss_improved":m["goal_bin_log_loss"] < m["goal_bin_log_loss_baseline"],
                "goal_bin_brier_improved":m["goal_bin_brier"] < m["goal_bin_brier_baseline"],
                "hda_brier_not_worse":m["hda_brier"] <= m["hda_brier_baseline"],
                "hda_log_loss_not_worse":m["hda_log_loss"] <= m["hda_log_loss_baseline"],
                "score1_rate":m["score1_hit_rate"],"score2_rate":m["score2_hit_rate"],
                "all_gates_pass":m["goal_bin_log_loss"] < m["goal_bin_log_loss_baseline"] and
                    m["hda_brier"] <= m["hda_brier_baseline"] and m["hda_log_loss"] <= m["hda_log_loss_baseline"]
            }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report["status"]=="BLOCKED": sys.exit(2)

if __name__=="__main__": main()
