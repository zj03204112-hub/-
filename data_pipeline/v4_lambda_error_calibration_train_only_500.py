#!/usr/bin/env python3
"""Train-only V4 lambda error diagnosis and calibration on frozen chronological 500.
Experiment only. Parameters are fitted on first 300 matches only; calibration and blind
holdout labels are never used to select parameters.
"""
import csv, json, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_pipeline"))
import v4_v5_joint_engine_validation_500 as base
import compare_models as cm

OUT_JSON = ROOT / "model_validation/v4_lambda_error_calibration_train_only_500_summary.json"
OUT_CSV = ROOT / "model_validation/v4_lambda_error_calibration_train_only_500.csv"
N = 8
EPS = 1e-12

def outcome(h, a):
    return "H" if h > a else "D" if h == a else "A"

def matrix(lh, la, rho=-0.05):
    lh, la = max(0.05, min(5.0, lh)), max(0.05, min(5.0, la))
    rows = []
    for i in range(N):
        row = []
        for j in range(N):
            tau = 1.0
            if i == 0 and j == 0: tau = 1 - lh * la * rho
            elif i == 0 and j == 1: tau = 1 + lh * rho
            elif i == 1 and j == 0: tau = 1 + la * rho
            elif i == 1 and j == 1: tau = 1 - rho
            row.append(math.exp(-lh) * lh**i / math.factorial(i) *
                       math.exp(-la) * la**j / math.factorial(j) * tau)
        rows.append(row)
    z = sum(map(sum, rows))
    return [[v / z for v in row] for row in rows]

def probs(m):
    p = {"H": 0.0, "D": 0.0, "A": 0.0}
    for i in range(N):
        for j in range(N):
            p["H" if i > j else "D" if i == j else "A"] += m[i][j]
    return p

def nll_score(m, h, a):
    return -math.log(max(EPS, m[h][a])) if h < N and a < N else -math.log(EPS)

def goal_diff_distribution(rows, sh=1.0, sa=1.0, rho=-0.05):
    keys = ("0", "1", "2", "3+")
    observed = {k: 0 for k in keys}
    predicted_sum = {k: 0.0 for k in keys}
    for x in rows:
        h, a = int(x["match"][4]), int(x["match"][5])
        d = abs(h-a)
        observed[str(d) if d <= 2 else "3+"] += 1
        m = matrix(x["v4"][0]*sh, x["v4"][1]*sa, rho)
        for k in keys:
            predicted_sum[k] += sum(m[i][j] for i in range(N) for j in range(N)
                                    if (abs(i-j) == int(k) if k != "3+" else abs(i-j) >= 3))
    n = len(rows)
    return {k: {"actual_count": observed[k], "actual_rate": observed[k]/n if n else None,
                "mean_predicted_probability": predicted_sum[k]/n if n else None,
                "probability_minus_actual_rate": predicted_sum[k]/n-observed[k]/n if n else None}
            for k in keys}

def metrics(rows, sh=1.0, sa=1.0, rho=-0.05):
    n = len(rows); correct = 0; tp = fp = fn = 0; brier = ll = snll = 0.0
    pred_counts = {k: 0 for k in "HDA"}
    actual_counts = {k: 0 for k in "HDA"}
    for x in rows:
        h, a = int(x["match"][4]), int(x["match"][5])
        p = probs(matrix(x["v4"][0] * sh, x["v4"][1] * sa, rho))
        y = outcome(h, a); pred = max("HDA", key=lambda k: p[k])
        correct += pred == y; pred_counts[pred] += 1; actual_counts[y] += 1
        tp += pred == "D" and y == "D"; fp += pred == "D" and y != "D"; fn += pred != "D" and y == "D"
        brier += sum((p[k] - float(k == y))**2 for k in "HDA")
        ll -= math.log(max(EPS, p[y]))
        snll += nll_score(matrix(x["v4"][0] * sh, x["v4"][1] * sa, rho), h, a)
    return {"n": n, "accuracy": correct/n if n else None, "correct": correct,
            "predicted_counts": pred_counts, "actual_counts": actual_counts,
            "draw_tp": tp, "draw_fp": fp, "draw_fn": fn,
            "draw_precision": tp/(tp+fp) if tp+fp else 0.0,
            "draw_recall": tp/(tp+fn) if tp+fn else 0.0,
            "brier": brier/n if n else None, "log_loss": ll/n if n else None,
            "score_nll": snll/n if n else None}

def mean(v):
    return sum(v)/len(v) if v else None

def bucket_gap(g):
    a = abs(g)
    return "0-.25" if a < .25 else ".25-.50" if a < .50 else ".50-.75" if a < .75 else ".75+"

def goal_bucket(h, a):
    d = abs(h-a)
    return str(d) if d <= 2 else "3+"

def main():
    rows = base.get_rows()
    rows.sort(key=lambda x: (x["match"][1], x["match"][0]))
    if len(rows) != 500 or len({x["match"][0] for x in rows}) != 500:
        raise RuntimeError("Frozen sample must be exactly 500 unique resolved matches")
    train, cal, hold = rows[:300], rows[300:400], rows[400:]
    # Fit only on train: grid search the two venue-specific lambda scales and DC rho
    # by exact-score negative log-likelihood. Calibration and holdout are not used here.
    scales = [round(0.80 + 0.025*i, 3) for i in range(13)]
    rhos = [-0.10, -0.075, -0.05, -0.025, 0.0, 0.025, 0.05]
    candidates = []
    for sh in scales:
        for sa in scales:
            for rho in rhos:
                loss = mean([nll_score(matrix(x["v4"][0]*sh, x["v4"][1]*sa, rho),
                                       int(x["match"][4]), int(x["match"][5])) for x in train])
                candidates.append({"home_scale": sh, "away_scale": sa, "rho": rho, "train_score_nll": loss})
    best = min(candidates, key=lambda z: (z["train_score_nll"],
                  abs(z["home_scale"]-1)+abs(z["away_scale"]-1), abs(z["rho"]+0.05)))
    sh, sa, rho = best["home_scale"], best["away_scale"], best["rho"]

    # Build stratified raw-forecast lambda diagnostics. Lambda error = predicted - actual.
    detail = []
    con = base.sqlite3.connect(str(ROOT / "football_model_database.sqlite"))
    train_ids = {z["match"][0] for z in train}
    cal_ids = {z["match"][0] for z in cal}
    for x in rows:
        m = x["match"]; h, a = int(m[4]), int(m[5])
        lh, la = float(x["v4"][0]), float(x["v4"][1])
        m0 = matrix(lh, la)
        mc = matrix(lh*sh, la*sa, rho)
        p0 = probs(m0)
        pc = probs(mc)
        gd0 = {k: sum(m0[i][j] for i in range(N) for j in range(N)
                      if (abs(i-j) == int(k) if k != "3+" else abs(i-j) >= 3)) for k in ("0","1","2","3+")}
        gdc = {k: sum(mc[i][j] for i in range(N) for j in range(N)
                      if (abs(i-j) == int(k) if k != "3+" else abs(i-j) >= 3)) for k in ("0","1","2","3+")}
        strength_h = cm.strength(con, m[2],
                                 (cm.datetime.fromisoformat(m[1][:19])-cm.timedelta(hours=12)).isoformat(timespec="seconds"))
        strength_a = cm.strength(con, m[3],
                                 (cm.datetime.fromisoformat(m[1][:19])-cm.timedelta(hours=12)).isoformat(timespec="seconds"))
        mapping = x["mapping"]
        detail.append({
            "match_id": m[0], "kickoff": m[1], "league": mapping.get("league") or mapping.get("competition_code") or "unknown",
            "home": m[2], "away": m[3], "actual_score": f"{h}-{a}", "actual_result": outcome(h,a),
            "actual_goal_diff_bucket": goal_bucket(h,a), "strength_gap_signed": round(strength_h-strength_a,6),
            "strength_gap_bucket": bucket_gap(strength_h-strength_a),
            "lambda_home_raw": lh, "lambda_away_raw": la, "lambda_total_raw": lh+la,
            "lambda_home_error_raw": lh-h, "lambda_away_error_raw": la-a, "lambda_total_error_raw": lh+la-h-a,
            "lambda_home_calibrated": lh*sh, "lambda_away_calibrated": la*sa,
            "lambda_home_error_calibrated": lh*sh-h, "lambda_away_error_calibrated": la*sa-a,
            "baseline_pred": max("HDA", key=lambda k:p0[k]), "baseline_p_home":p0["H"],
            "baseline_p_draw":p0["D"], "baseline_p_away":p0["A"],
            "calibrated_pred": max("HDA", key=lambda k:pc[k]), "calibrated_p_home":pc["H"],
            "calibrated_p_draw":pc["D"], "calibrated_p_away":pc["A"],
            "baseline_p_goal_diff_0":gd0["0"], "baseline_p_goal_diff_1":gd0["1"],
            "baseline_p_goal_diff_2":gd0["2"], "baseline_p_goal_diff_3plus":gd0["3+"],
            "calibrated_p_goal_diff_0":gdc["0"], "calibrated_p_goal_diff_1":gdc["1"],
            "calibrated_p_goal_diff_2":gdc["2"], "calibrated_p_goal_diff_3plus":gdc["3+"],
            "split": "train" if m[0] in train_ids else "calibration" if m[0] in cal_ids else "blind_holdout"
        })
    con.close()
    # Group error summaries.
    groups = {}
    for key in ("league", "strength_gap_bucket", "actual_goal_diff_bucket"):
        groups[key] = {}
        for label in sorted(set(str(d[key]) for d in detail)):
            q = [d for d in detail if str(d[key]) == label]
            groups[key][label] = {"n":len(q),
                "mean_actual_home_goals":mean([float(d["actual_score"].split("-")[0]) for d in q]),
                "mean_actual_away_goals":mean([float(d["actual_score"].split("-")[1]) for d in q]),
                "mean_lambda_home_raw":mean([d["lambda_home_raw"] for d in q]),
                "mean_lambda_away_raw":mean([d["lambda_away_raw"] for d in q]),
                "mean_home_lambda_error_raw":mean([d["lambda_home_error_raw"] for d in q]),
                "mean_away_lambda_error_raw":mean([d["lambda_away_error_raw"] for d in q]),
                "mean_home_lambda_error_calibrated":mean([d["lambda_home_error_calibrated"] for d in q]),
                "mean_away_lambda_error_calibrated":mean([d["lambda_away_error_calibrated"] for d in q])}
    summary = {
        "experiment":"V4 lambda error diagnosis; calibration fitted only on first 300 chronological matches",
        "status":"EXPERIMENT_ONLY",
        "sample_n":500, "split":{"train":300,"calibration":100,"blind_holdout":100,
          "train_end":train[-1]["match"][1],"calibration_start":cal[0]["match"][1],
          "calibration_end":cal[-1]["match"][1],"holdout_start":hold[0]["match"][1]},
        "raw_train_only_fit_selected":{"home_lambda_scale":sh,"away_lambda_scale":sa,"dixon_coles_rho":rho,
          "train_score_nll":best["train_score_nll"],"candidate_count":len(candidates)},
        "baseline":{"train":metrics(train),"calibration":metrics(cal),"blind_holdout":metrics(hold)},
        "calibrated":{"train":metrics(train,sh,sa,rho),"calibration":metrics(cal,sh,sa,rho),
          "blind_holdout":metrics(hold,sh,sa,rho)},
        "stratified_lambda_error_diagnostics":groups,
        "goal_difference_distribution": {
          "train_300": {"baseline":goal_diff_distribution(train), "calibrated":goal_diff_distribution(train,sh,sa,rho)},
          "calibration_100": {"baseline":goal_diff_distribution(cal), "calibrated":goal_diff_distribution(cal,sh,sa,rho)},
          "blind_holdout_100": {"baseline":goal_diff_distribution(hold), "calibrated":goal_diff_distribution(hold,sh,sa,rho)},
          "all_500_descriptive_only": {"baseline":goal_diff_distribution(rows), "calibrated":goal_diff_distribution(rows,sh,sa,rho)}
        },
        "acceptance":"No production promotion in this experiment. Compare blind holdout accuracy/Brier/LogLoss/score-NLL and inspect group stability; calibration parameters were not selected on calibration or holdout labels."
    }
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(detail[0].keys())); w.writeheader(); w.writerows(detail)
    OUT_JSON.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
