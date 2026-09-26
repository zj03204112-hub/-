import json, math, sqlite3, sys, os
from datetime import datetime, timedelta

sys.path.insert(0, "data_pipeline")
from compare_models import model_lambdas, real_strength_metrics

DB = "football_model_database.sqlite"
OUT = os.environ.get("DRAW_ANALYSIS_OUT", "data/draw_cause_analysis_128.json")
N = int(os.environ.get("DRAW_ANALYSIS_N", "128"))

def cutoff12(kickoff):
    return (datetime.fromisoformat(kickoff[:19]) - timedelta(hours=12)).isoformat(timespec="seconds")

def bucket_strength_gap(h, a):
    gap = abs(h["score"] - a["score"])
    if gap < 0.08:
        return "close"
    if gap < 0.18:
        return "medium"
    return "large"

def schedule_row(con, mid, side):
    return con.execute("""SELECT next_opponent,next_match_time,rest_days,next_match_importance,
        rotation_risk,motivation_adjustment
        FROM schedule_intent WHERE match_id=? AND team_side=?""",(mid,side)).fetchone()

def injury_rows(con, mid, side, cut):
    return con.execute("""SELECT player,status,reason,position,availability_prob,
        expected_start_prob,impact_attack,impact_defense,data_cutoff,source_status
        FROM injuries
        WHERE match_id=? AND team_side=?
          AND (data_cutoff IS NULL OR data_cutoff<=?)
        ORDER BY COALESCE(impact_attack,0)+COALESCE(impact_defense,0) DESC,
                 COALESCE(availability_prob,1) ASC""",(mid,side,cut)).fetchall()

def lineup_row(con, mid, side, cut):
    return con.execute("""SELECT expected_start_strength,attack_delta,defense_delta,
        uncertainty,player_count,data_cutoff,source_status
        FROM lineup_projection
        WHERE match_id=? AND team_side=? AND data_cutoff<=?""",(mid,side,cut)).fetchone()

def importance_score(x):
    if x is None: return 0.0
    s=str(x).lower()
    if any(k in s for k in ("final","finale","playoff","derby","champion","relegation","title")):
        return 1.0
    if any(k in s for k in ("high","important","critical","key")):
        return 0.8
    if any(k in s for k in ("medium","normal")):
        return 0.5
    if any(k in s for k in ("low","minor")):
        return 0.25
    return 0.5

def classify(sr, ar, sh, sa, ih, ia, lh, la):
    reasons=[]
    if bucket_strength_gap(sh,sa)=="close":
        reasons.append("strength_close")
    if max(float(sr[4] or 0) if sr else 0, float(ar[4] or 0) if ar else 0) >= 0.35:
        reasons.append("rotation")
    if (sr and float(sr[2] or 99)<4) or (ar and float(ar[2] or 99)<4):
        reasons.append("schedule_density")
    if (ih or ia):
        material=[]
        for r in (ih or [])+(ia or []):
            ap=r[4]
            aa=r[6] or 0
            da=r[7] or 0
            if (ap is not None and float(ap)<0.5) or abs(float(aa))+abs(float(da))>=0.08:
                material.append(r)
        if material:
            reasons.append("key_absence")
    if (sr and importance_score(sr[3])>=0.8) or (ar and importance_score(ar[3])>=0.8):
        reasons.append("next_match_importance")
    if not reasons:
        reasons.append("unconfirmed")
    return reasons

def main():
    con=sqlite3.connect(DB)
    cols={r[1] for r in con.execute("PRAGMA table_info(injuries)").fetchall()}
    if not cols:
        raise SystemExit("INJURY_TABLE_MISSING")
    matches=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,
        r.ft_home,r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.status='finished' AND r.ft_home IS NOT NULL
          AND r.ft_home=r.ft_away
        ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?""",(N,)).fetchall()
    rows=[]; counts={}
    for m in matches:
        mid,ko,home,away,fh,fa=m
        cut=cutoff12(ko)
        sh=real_strength_metrics(con,home,cut); sa=real_strength_metrics(con,away,cut)
        sr=schedule_row(con,mid,"home"); ar=schedule_row(con,mid,"away")
        ih=injury_rows(con,mid,"home",cut); ia=injury_rows(con,mid,"away",cut)
        lh=lineup_row(con,mid,"home",cut); la=lineup_row(con,mid,"away",cut)
        reasons=classify(sr,ar,sh,sa,ih,ia,lh,la)
        for r in reasons: counts[r]=counts.get(r,0)+1
        # Record V4 pre-match probabilities for diagnosis only; do not alter them.
        dummy=(mid,ko,home,away,fh,fa)
        try:
            lam_h,lam_a=model_lambdas(con,"v4",dummy)
            p=math.exp(-lam_h) * math.exp(-lam_a)
            # Use a full matrix through compare_models' API without importing another copy.
            from compare_models import matrix, N as GOAL_N
            mat=matrix(lam_h,lam_a,True)
            ph=sum(mat[i][j] for i in range(GOAL_N) for j in range(GOAL_N) if i>j)
            pd=sum(mat[i][j] for i in range(GOAL_N) for j in range(GOAL_N) if i==j)
            pa=sum(mat[i][j] for i in range(GOAL_N) for j in range(GOAL_N) if i<j)
        except Exception:
            lam_h=lam_a=None; ph=pd=pa=None
        def inj_out(xs):
            return [{"player":r[0],"status":r[1],"reason":r[2],"position":r[3],
                     "availability_prob":r[4],"expected_start_prob":r[5],
                     "impact_attack":r[6],"impact_defense":r[7],
                     "data_cutoff":r[8],"source_status":r[9]} for r in xs]
        rows.append({
            "match_id":mid,"kickoff":ko,"home":home,"away":away,"actual_score":f"{fh}-{fa}",
            "data_cutoff":cut,
            "strength":{"home":sh,"away":sa,"gap":round(abs(sh["score"]-sa["score"]),4),
                        "bucket":bucket_strength_gap(sh,sa)},
            "schedule_intent":{"home":sr,"away":ar},
            "injuries":{"home":inj_out(ih),"away":inj_out(ia),
                        "records":len(ih)+len(ia)},
            "lineup_proxy":{"home":lh,"away":la},
            "v4_pre_match":{"lambda_home":lam_h,"lambda_away":lam_a,
                            "H":ph,"D":pd,"A":pa},
            "cause_tags":reasons
        })
    summary={"n_draws":len(rows),"counts":counts,
             "share":{k:round(v/max(1,len(rows)),4) for k,v in counts.items()},
             "injury_records":sum(x["injuries"]["records"] for x in rows),
             "draws_with_injury_records":sum(x["injuries"]["records"]>0 for x in rows),
             "draws_with_rotation_signal":sum("rotation" in x["cause_tags"] for x in rows),
             "draws_strength_close":sum(x["strength"]["bucket"]=="close" for x in rows)}
    out={"definition":"128 most recent completed actual draws; causes are classified only from pre-match/T-12h repository data. No draw-probability adjustment is applied.","summary":summary,"matches":rows}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps(summary,ensure_ascii=False))
    if len(rows)<N: raise SystemExit(f"DRAW_ANALYSIS_NEEDS_{N}:{len(rows)}")
    con.close()

if __name__=="__main__":
    main()
