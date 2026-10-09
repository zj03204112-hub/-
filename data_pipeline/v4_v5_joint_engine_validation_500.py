#!/usr/bin/env python3
"""Leakage-aware V4 vs independent V5-SCORE reconstruction vs lambda-level blend.
V5 here is an explicitly documented reconstruction, not recovered original weights.
"""
import csv, json, math, sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "football_model_database.sqlite"
MAP = ROOT / "model_validation/500_match_to_db_match_id_mapping.csv"
OUT_CSV = ROOT / "model_validation/v4_v5_joint_walkforward_500.csv"
OUT_JSON = ROOT / "model_validation/v4_v5_joint_walkforward_500_summary.json"
sys.path.insert(0, str(ROOT / "data_pipeline"))
import compare_models as cm

N = 8
RHO = -0.05
HIST_N = 20
WEIGHTS = [i / 20 for i in range(21)]  # w = V4 share in log-lambda blend

def outcome(score):
    h, a = score
    return "H" if h > a else "D" if h == a else "A"

def matrix(lh, la):
    rows = []
    for i in range(N):
        row = []
        for j in range(N):
            tau = 1.0
            if i == 0 and j == 0: tau = 1 - lh * la * RHO
            elif i == 0 and j == 1: tau = 1 + lh * RHO
            elif i == 1 and j == 0: tau = 1 + la * RHO
            elif i == 1 and j == 1: tau = 1 - RHO
            p = math.exp(-lh) * lh**i / math.factorial(i) * math.exp(-la) * la**j / math.factorial(j) * tau
            row.append(p)
        rows.append(row)
    z = sum(map(sum, rows))
    return [[v / z for v in row] for row in rows]

def matrix_probs(m):
    p = {"H":0.0,"D":0.0,"A":0.0}
    for i in range(N):
        for j in range(N):
            p["H" if i > j else "D" if i == j else "A"] += m[i][j]
    return p

def top2(m):
    return sorted(((m[i][j], i, j) for i in range(N) for j in range(N)), reverse=True)[:2]

def brier(p, actual):
    return sum((p[k] - float(k == actual))**2 for k in ("H","D","A"))

def logloss(p, actual):
    return -math.log(max(1e-12, p[actual]))

def score_nll(m, score):
    h, a = score
    if h < N and a < N: return -math.log(max(1e-12, m[h][a]))
    return -math.log(1e-12)

def weighted_mean(vals):
    if not vals: return None
    weights = [math.exp(-0.08 * i) for i in range(len(vals))]
    return sum(v * weights[i] for i, v in enumerate(vals)) / sum(weights)

def get_league_col(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(matches)")}
    return next((c for c in ("league","league_code","competition_code") if c in cols), None)

def load_history(con, cutoff, league_col, league):
    sql = """SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away"""
    if league_col: sql += f",m.{league_col}"
    sql += """ FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff < ? AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
      ORDER BY m.kickoff DESC,m.match_id DESC"""
    rows = con.execute(sql, (cutoff,)).fetchall()
    if league_col and league:
        filtered = [r for r in rows if str(r[5] or "").strip().lower() == str(league).strip().lower()]
        if len(filtered) >= 30: rows = filtered
    return rows

def v5_reconstructed_lambdas(con, match, league_col, sample_league):
    """Independent reconstruction: venue-specific weighted attack/defence ratios
    over league goal baselines, with opponent-strength normalization and T-12h cutoff.
    """
    mid, kickoff, home, away, fh, fa = match
    cutoff = (datetime.fromisoformat(kickoff[:19]) - timedelta(hours=12)).isoformat(timespec="seconds")
    hist = load_history(con, cutoff, league_col, sample_league)
    if not hist:
        return 1.45, 1.15, cutoff, 0
    # Rows are newest first; only pre-cutoff matches are present.
    home_goals = [float(r[3]) for r in hist]
    away_goals = [float(r[4]) for r in hist]
    league_h = max(0.45, weighted_mean(home_goals[:200]) or 1.45)
    league_a = max(0.35, weighted_mean(away_goals[:200]) or 1.15)

    # Team's recent overall scoring/conceding ratios, used to normalize opponents.
    team_cache = {}
    def overall(team):
        if team in team_cache: return team_cache[team]
        gf, ga = [], []
        for r in hist:
            _, h, a, gh, ga_ = r[:5]
            if h == team: gf.append(float(gh)); ga.append(float(ga_))
            elif a == team: gf.append(float(ga_)); ga.append(float(gh))
            if len(gf) >= HIST_N: break
        result = (weighted_mean(gf) or league_h, weighted_mean(ga) or league_a)
        team_cache[team] = result
        return result

    def venue_rates(team, venue):
        gf, ga = [], []
        for r in hist:
            _, h, a, gh, ga_ = r[:5]
            if venue == "home" and h == team:
                opp = a; xgf, xga = float(gh), float(ga_)
            elif venue == "away" and a == team:
                opp = h; xgf, xga = float(ga_), float(gh)
            else:
                continue
            opp_gf, opp_ga = overall(opp)
            # Opponent attack/defence normalization; cap corrections to avoid instability.
            def_ratio = max(0.65, min(1.50, opp_ga / league_h))
            att_ratio = max(0.65, min(1.50, opp_gf / league_h))
            gf.append(xgf / def_ratio)
            ga.append(xga / att_ratio)
            if len(gf) >= HIST_N: break
        return weighted_mean(gf), weighted_mean(ga), len(gf)

    hgf, hga, nh = venue_rates(home, "home")
    agf, aga, na = venue_rates(away, "away")
    h_attack = max(0.20, min(2.50, (hgf or league_h) / league_h))
    h_defence = max(0.20, min(2.50, (hga or league_a) / league_a))
    a_attack = max(0.20, min(2.50, (agf or league_a) / league_a))
    a_defence = max(0.20, min(2.50, (aga or league_h) / league_h))
    # Venue baseline * own attack strength * opponent defensive concession strength.
    lh = max(0.15, min(4.0, league_h * h_attack * a_defence))
    la = max(0.12, min(3.5, league_a * a_attack * h_defence))
    return lh, la, cutoff, min(nh, na)

def get_rows():
    con = sqlite3.connect(DB)
    cm.HIST_LIMIT = HIST_N
    league_col = get_league_col(con)
    with MAP.open(encoding="utf-8-sig", newline="") as f:
        mapping = list(csv.DictReader(f))
    if len(mapping) != 500 or len({r["match_id"] for r in mapping}) != 500:
        raise RuntimeError("Frozen sample must contain exactly 500 unique match_id rows")
    if any(r.get("mapping_status") != "unique" for r in mapping):
        raise RuntimeError("Frozen mapping has unresolved or ambiguous rows")
    db = {r[0]:r for r in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL""")}
    rows = []
    for z in mapping:
        r = db.get(z["match_id"])
        if not r: raise RuntimeError(f"Mapping match_id missing in DB: {z['match_id']}")
        # Check mapping and DB actuals agree; do not silently repair mismatches.
        if (str(r[2]).strip().lower() != str(z["db_home"]).strip().lower()
            or str(r[3]).strip().lower() != str(z["db_away"]).strip().lower()
            or int(r[4]) != int(z["db_home_score"]) or int(r[5]) != int(z["db_away_score"])):
            raise RuntimeError(f"Frozen mapping identity/score mismatch: {z['match_id']}")
        league = z.get("competition_code") or z.get("league") or ""
        v4h, v4a = cm.model_lambdas(con, "v4", r)
        v5h, v5a, cutoff, venue_n = v5_reconstructed_lambdas(con, r, league_col, league)
        m4, m5 = matrix(v4h, v4a), matrix(v5h, v5a)
        rows.append({"mapping":z,"match":r,"actual":outcome((int(r[4]),int(r[5]))),
                     "v4":[v4h,v4a],"v5":[v5h,v5a],"m4":m4,"m5":m5,
                     "cutoff":cutoff,"venue_history_n":venue_n})
    con.close()
    return rows

def score_arm(rows, arm, w=None):
    detail=[]; agg={"n":0,"correct":0,"draw_tp":0,"draw_fp":0,"draw_fn":0,
                    "brier":0.0,"logloss":0.0,"score1":0,"score2":0,"score_nll":0.0}
    for x in rows:
        if arm == "v4": m=x["m4"]
        elif arm == "v5": m=x["m5"]
        else:
            # Integrate at the goal-rate layer, not by stitching final labels and score outputs.
            lh=math.exp(w*math.log(max(1e-9,x["v4"][0]))+(1-w)*math.log(max(1e-9,x["v5"][0])))
            la=math.exp(w*math.log(max(1e-9,x["v4"][1]))+(1-w)*math.log(max(1e-9,x["v5"][1])))
            m=matrix(lh,la)
        p=matrix_probs(m); pred=max(p,key=p.get); a=x["actual"]
        scores=top2(m); actual_score=(int(x["match"][4]),int(x["match"][5]))
        hit1=(scores[0][1],scores[0][2])==actual_score
        hit2=(scores[1][1],scores[1][2])==actual_score
        agg["n"]+=1; agg["correct"]+=int(pred==a)
        agg["draw_tp"]+=int(pred=="D" and a=="D")
        agg["draw_fp"]+=int(pred=="D" and a!="D")
        agg["draw_fn"]+=int(pred!="D" and a=="D")
        agg["brier"]+=brier(p,a); agg["logloss"]+=logloss(p,a)
        agg["score1"]+=int(hit1); agg["score2"]+=int(hit2); agg["score_nll"]+=score_nll(m,actual_score)
        detail.append({"match_id":x["match"][0],"kickoff":x["match"][1],
            "home":x["match"][2],"away":x["match"][3],"actual_score":f"{actual_score[0]}-{actual_score[1]}",
            "actual_90":a,"arm":arm,"lambda_home":x["v4"][0] if arm=="v4" else (x["v5"][0] if arm=="v5" else None),
            "lambda_away":x["v4"][1] if arm=="v4" else (x["v5"][1] if arm=="v5" else None),
            "score1":f"{scores[0][1]}-{scores[0][2]}","score2":f"{scores[1][1]}-{scores[1][2]}",
            "pred_90":pred,"p_home":p["H"],"p_draw":p["D"],"p_away":p["A"],
            "score1_hit":int(hit1),"score2_hit":int(hit2),"logloss":logloss(p,a),"brier":brier(p,a)})
    n=agg["n"]
    agg["accuracy"]=agg["correct"]/n if n else None
    agg["score1_hit_rate"]=agg["score1"]/n if n else None
    agg["score2_hit_rate"]=agg["score2"]/n if n else None
    agg["score_nll"]=agg["score_nll"]/n if n else None
    agg["brier"]=agg["brier"]/n if n else None
    agg["logloss"]=agg["logloss"]/n if n else None
    agg["draw_precision"]=agg["draw_tp"]/(agg["draw_tp"]+agg["draw_fp"]) if agg["draw_tp"]+agg["draw_fp"] else 0.0
    agg["draw_recall"]=agg["draw_tp"]/(agg["draw_tp"]+agg["draw_fn"]) if agg["draw_tp"]+agg["draw_fn"] else 0.0
    agg["draw_f1"]=2*agg["draw_precision"]*agg["draw_recall"]/(agg["draw_precision"]+agg["draw_recall"]) if agg["draw_precision"]+agg["draw_recall"] else 0.0
    agg["macro_f1"] = macro_f1(detail)
    return agg, detail

def macro_f1(detail):
    vals=[]
    for c in ("H","D","A"):
        tp=sum(r["pred_90"]==c and r["actual_90"]==c for r in detail)
        fp=sum(r["pred_90"]==c and r["actual_90"]!=c for r in detail)
        fn=sum(r["pred_90"]!=c and r["actual_90"]==c for r in detail)
        pr=tp/(tp+fp) if tp+fp else 0; re=tp/(tp+fn) if tp+fn else 0
        vals.append(2*pr*re/(pr+re) if pr+re else 0)
    return sum(vals)/3

def main():
    rows=get_rows()
    rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    n=len(rows); a=int(n*0.60); b=int(n*0.80)
    train,cal,test=rows[:a],rows[a:b],rows[b:]
    # Fit blend weight only on first chronological 60%, minimize exact-score NLL.
    candidates=[]
    for w in WEIGHTS:
        vals=[]
        for x in train:
            lh=math.exp(w*math.log(max(1e-9,x["v4"][0]))+(1-w)*math.log(max(1e-9,x["v5"][0])))
            la=math.exp(w*math.log(max(1e-9,x["v4"][1]))+(1-w)*math.log(max(1e-9,x["v5"][1])))
            vals.append(score_nll(matrix(lh,la), (int(x["match"][4]),int(x["match"][5]))))
        candidates.append((sum(vals)/len(vals),w))
    fit_nll,w=min(candidates)
    all_detail=[]; summary={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":n,
      "method":"independent V5-SCORE reconstructed engine; V4/V5 λ-level log blend; chronological 60/20/20 split",
      "v5_engine":"transparent reconstruction; not recovered original V5 parameters",
      "cutoff_rule":"each match only uses matches with kickoff earlier than its own T-12h cutoff",
      "split":{"train_n":len(train),"calibration_n":len(cal),"final_holdout_n":len(test)},
      "fit":{"objective":"exact-score negative log-likelihood","grid_v4_share":WEIGHTS,
             "selected_v4_share":w,"selected_v5_share":1-w,"train_score_nll":fit_nll},
      "arms":{},"final_holdout_gate":"Do not promote unless joint arm improves on both V4 and V5 baseline on final holdout and probability metrics do not materially deteriorate."}
    for arm in ("v4","v5","joint"):
        metrics_by_split={}
        for label, subset in (("train",train),("calibration",cal),("final_holdout",test)):
            met,detail=score_arm(subset,arm,w if arm=="joint" else None)
            metrics_by_split[label]=met
            for d in detail: d["split"]=label; d["joint_v4_share"]=w if arm=="joint" else ""
            all_detail.extend(detail)
        summary["arms"][arm]=metrics_by_split
    v4=summary["arms"]["v4"]["final_holdout"]
    v5=summary["arms"]["v5"]["final_holdout"]
    joint=summary["arms"]["joint"]["final_holdout"]
    summary["decision"]={
      "joint_accuracy_delta_vs_v4":joint["accuracy"]-v4["accuracy"],
      "joint_accuracy_delta_vs_v5":joint["accuracy"]-v5["accuracy"],
      "joint_brier_delta_vs_v4":joint["brier"]-v4["brier"],
      "joint_brier_delta_vs_v5":joint["brier"]-v5["brier"],
      "joint_logloss_delta_vs_v4":joint["logloss"]-v4["logloss"],
      "joint_logloss_delta_vs_v5":joint["logloss"]-v5["logloss"],
      "joint_beats_both_accuracy":joint["accuracy"]>v4["accuracy"] and joint["accuracy"]>v5["accuracy"],
      "production_status":"EXPERIMENT_ONLY"}
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(all_detail[0].keys()));wr.writeheader();wr.writerows(all_detail)
    OUT_JSON.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
