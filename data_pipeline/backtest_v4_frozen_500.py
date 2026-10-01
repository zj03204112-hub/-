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
MAPPING_PATH="model_validation/500_match_to_db_match_id_mapping.csv"

def load_mapping():
    with open(MAPPING_PATH,encoding="utf-8-sig",newline="") as f:
        m=list(csv.DictReader(f))
    if len(m)!=500 or any(r["mapping_status"]!="unique" for r in m) or len({r["match_id"] for r in m})!=500:
        raise RuntimeError("FROZEN_MAPPING_NOT_STRICT_500")
    return m

mapping=load_mapping()
with open(CSV_PATH,encoding="utf-8-sig",newline="") as f:
    src=list(csv.DictReader(f))
match_rows=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL""").fetchall()
by_id={m[0]:m for m in match_rows}
rows=[]
for r in mapping:
    src_row=src[int(r["sample_row"])-1]
    if r["match_id"] not in by_id:
        raise RuntimeError(f"DB_MATCH_ID_MISSING {r['match_id']}")
    match=by_id[r["match_id"]]
    if match[1][:10]!=r["date"] or match[4]!=int(r["home_score"]) or match[5]!=int(r["away_score"]):
        raise RuntimeError(f"FROZEN_MAPPING_MISMATCH sample={r['sample_row']} match_id={r['match_id']}")
    p,actual=cm.run_model(con,"v4",match)
    lh,la=cm.model_lambdas(con,"v4",match)
    rs_h=cm.real_strength_metrics(con,match[2],(datetime.fromisoformat(match[1][:19])-timedelta(hours=12)).isoformat(timespec="seconds"))
    rs_a=cm.real_strength_metrics(con,match[3],(datetime.fromisoformat(match[1][:19])-timedelta(hours=12)).isoformat(timespec="seconds"))
    ts=top_scores(lh,la)
    pred=max(p,key=p.get)
    rows.append({
        "sample_row":r["sample_row"],"date":r["date"],"league":r["league"],"home":r["home"],"away":r["away"],
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
print(json.dumps({"rows":len(rows),"output":OUT,"mapping":"league+date+normalized_teams+final_score","model":"V4 frozen current code","t12h":True},ensure_ascii=False))
