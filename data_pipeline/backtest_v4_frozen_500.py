import csv, json, math, sqlite3
from datetime import datetime, timedelta
import sys
sys.path.insert(0, "data_pipeline")
import compare_models as cm

CSV_PATH="model_validation/500_match_base_batch01.csv"
OUT="model_validation/500_match_V4_frozen_predictions.csv"
DB="football_model_database.sqlite"

def outcome(fh,fa):
    return "H" if fh>fa else "D" if fh==fa else "A"

def top_scores(lh,la):
    m=cm.matrix(lh,la,True)
    cells=[]
    for i in range(cm.N):
        for j in range(cm.N):
            cells.append((m[i][j],i,j))
    cells.sort(reverse=True)
    return cells[:2]

con=sqlite3.connect(DB)
rows=[]
with open(CSV_PATH,encoding="utf-8-sig",newline="") as f:
    src=list(csv.DictReader(f))
matches=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
ORDER BY m.kickoff,m.match_id""").fetchall()

idx={}
for m in matches:
    key=(m[1][:10],m[2].strip(),m[3].strip())
    idx.setdefault(key,[]).append(m)

for r in src:
    key=(r["date"],r["home"].strip(),r["away"].strip())
    cand=idx.get(key,[])
    if len(cand)!=1:
        raise RuntimeError(f"DB_MATCH_MAPPING_FAILED {key} candidates={len(cand)}")
    match=cand[0]
    p,actual=cm.run_model(con,"v4",match)
    lh,la=cm.model_lambdas(con,"v4",match)
    rs_h=cm.real_strength_metrics(con,match[2],(datetime.fromisoformat(match[1][:19])-timedelta(hours=12)).isoformat(timespec="seconds"))
    rs_a=cm.real_strength_metrics(con,match[3],(datetime.fromisoformat(match[1][:19])-timedelta(hours=12)).isoformat(timespec="seconds"))
    ts=top_scores(lh,la)
    pred=max(p,key=p.get)
    rows.append({
        "date":r["date"],"league":r["league"],"home":r["home"],"away":r["away"],
        "actual_result":actual,"home_score":int(r["home_score"]),"away_score":int(r["away_score"]),
        "pred_result":pred,"p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),
        "confidence":round(max(p.values()),6),
        "pred_score_1":f"{ts[0][1]}-{ts[0][2]}","pred_score_1_prob":round(ts[0][0],6),
        "pred_score_2":f"{ts[1][1]}-{ts[1][2]}","pred_score_2_prob":round(ts[1][0],6),
        "pred_goal_diff":round(lh-la,6),"actual_goal_diff":int(r["home_score"])-int(r["away_score"]),
        "goal_diff_abs_error":round(abs((lh-la)-(int(r["home_score"])-int(r["away_score"]))),6),
        "strength_home":rs_h["score"],"strength_away":rs_a["score"],
        "strength_gap":round(abs(rs_h["score"]-rs_a["score"]),6),
        "strength_gap_signed":round(rs_h["score"]-rs_a["score"],6),
        "match_id":match[0],"kickoff":match[1]
    })

if len(rows)!=500 or len({(x["date"],x["league"],x["home"],x["away"]) for x in rows})!=500:
    raise RuntimeError(f"FROZEN_SAMPLE_INTEGRITY_FAILED n={len(rows)}")
fields=list(rows[0].keys())
with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
print(json.dumps({"rows":len(rows),"output":OUT,"mapping":"500/500","model":"V4 frozen current code","t12h":True},ensure_ascii=False))
