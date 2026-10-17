#!/usr/bin/env python3
"""Rebuild V5.2 DB-native match-level backtest CSV from the committed SQLite DB.
Read-only to the database; writes only the derived CSV output.
"""
import csv, json, math, sqlite3, sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, "data_pipeline")
from compare_models import model_lambdas, matrix, N

DB = Path("football_model_database.sqlite")
OUT = Path("model_validation/v52_jincai_20260901_20261013/v52_db_native_predictions_backtest_20260901_20261013.csv")
START, END = "2026-09-01", "2026-10-13"

def outcome(h, a):
    return "H" if h > a else "D" if h == a else "A"

def main():
    if not DB.exists():
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
      SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
        AND date(m.kickoff) BETWEEN ? AND ?
      ORDER BY m.kickoff,m.match_id
    """, (START, END)).fetchall()
    if len(rows) != 398:
        raise SystemExit(f"EXPECTED_398_MATCHES_GOT_{len(rows)}")
    out=[]
    for row in rows:
        match=(row["match_id"],row["kickoff"],row["home_team"],row["away_team"],row["ft_home"],row["ft_away"])
        cutoff=(datetime.fromisoformat(row["kickoff"][:19])-timedelta(hours=12)).isoformat(timespec="seconds")
        lh,la=model_lambdas(con,"v5.2",match)
        mat=matrix(lh,la,True)
        probs={
          "H":sum(mat[i][j] for i in range(N) for j in range(N) if i>j),
          "D":sum(mat[i][j] for i in range(N) for j in range(N) if i==j),
          "A":sum(mat[i][j] for i in range(N) for j in range(N) if i<j)
        }
        scores=sorted(((mat[i][j],i,j) for i in range(N) for j in range(N)),reverse=True)
        s1,s2=scores[0],scores[1]
        actual=outcome(row["ft_home"],row["ft_away"])
        pred=max(probs,key=probs.get)
        out.append({
          "match_id":row["match_id"],"kickoff":row["kickoff"],"date":row["kickoff"][:10],
          "home_team":row["home_team"],"away_team":row["away_team"],
          "actual_home_goals":row["ft_home"],"actual_away_goals":row["ft_away"],
          "actual_score":f'{row["ft_home"]}-{row["ft_away"]}',"actual_result":actual,
          "pred_score1":f"{s1[1]}-{s1[2]}","pred_score1_probability":round(s1[0],8),
          "pred_score2":f"{s2[1]}-{s2[2]}","pred_score2_probability":round(s2[0],8),
          "lambda_home":round(lh,8),"lambda_away":round(la,8),
          "p_home":round(probs["H"],8),"p_draw":round(probs["D"],8),"p_away":round(probs["A"],8),
          "pred_result":pred,"correct_1x2":int(pred==actual),
          "score1_exact":int((s1[1],s1[2])==(row["ft_home"],row["ft_away"])),
          "data_cutoff_T_minus_12h":cutoff,"sample_source":"V5.2_DB_native_reconstructed",
          "reconstruction_note":"Recomputed from committed SQLite DB and data_pipeline/compare_models.py; not the missing original local CSV."
        })
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)
    n=len(out)
    acc=sum(r["correct_1x2"] for r in out)
    exact=sum(r["score1_exact"] for r in out)
    brier=logloss=0.0
    for r in out:
        actual=r["actual_result"]
        p={"H":r["p_home"],"D":r["p_draw"],"A":r["p_away"]}
        for k in p: brier+=(p[k]-(1 if k==actual else 0))**2
        logloss-=math.log(max(1e-12,p[actual]))
    report={"n":n,"correct_1x2":acc,"accuracy_1x2":round(acc/n,4),
            "score1_exact_hits":exact,"score1_exact_rate":round(exact/n,4),
            "brier":round(brier/n,4),"logloss":round(logloss/n,4),
            "csv":str(OUT),"note":"Reconstructed row-level replay; compare with prior summary before treating as exact original artifact."}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    (OUT.parent/"v52_db_native_reconstruction_check_20260901_20261013.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    con.close()

if __name__=="__main__": main()
