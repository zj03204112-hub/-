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
# Build an identity-safe candidate index. Frozen CSV is the source of truth.
def norm_team(s):
    s=(s or "").strip().lower()
    for a,b in {"fc":"","cf":"","afc":"","sc":"","fk":"","ac":"","calcio":"","club":"","足球俱乐部":""}.items():
        s=s.replace(a,b)
    return "".join(ch for ch in s if ch.isalnum())

def db_league_column(con):
    cols=[r[1] for r in con.execute("PRAGMA table_info(matches)").fetchall()]
    for c in ("league","league_name","competition","competition_name","tournament","competition_code"):
        if c in cols: return c
    return None

league_col=db_league_column(con)
select_league=f",m.{league_col}" if league_col else ",NULL"
matches=con.execute(f"""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away{select_league}
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
ORDER BY m.kickoff,m.match_id""").fetchall()

idx={}
for m in matches:
    key=(str(m[6] or "").strip(),m[1][:10],norm_team(m[2]),norm_team(m[3]),m[4],m[5])
    idx.setdefault(key,[]).append(m)

for r in src:
    key=(r["date"],r["home"].strip(),r["away"].strip())
    score=(int(r["home_score"]),int(r["away_score"]))
    league=r["league"].strip()
    cand=idx.get((league,r["date"],norm_team(r["home"]),norm_team(r["away"]),score[0],score[1]),[])
    if not cand:
        aliases={
            "英超":{"英超","EPL","Premier League"},
            "西甲":{"西甲","La Liga","LaLiga"},
            "德甲":{"德甲","Bundesliga"},
            "意甲":{"意甲","Serie A"},
            "法甲":{"法甲","Ligue 1"},
            "韩职":{"韩职","K League 1","K1"},
            "日职J1":{"日职J1","J1 League","J1"}
        }
        allowed=aliases.get(league,{league})
        cand=[m for m in matches
              if str(m[6] or "").strip() in allowed
              and m[1][:10]==r["date"]
              and norm_team(m[2])==norm_team(r["home"])
              and norm_team(m[3])==norm_team(r["away"])
              and m[4]==score[0] and m[5]==score[1]]
    if len(cand)!=1:
        raise RuntimeError(
            f"DB_MATCH_MAPPING_FAILED {key} candidates={len(cand)} "
            f"league_column={league_col} score={score}"
        )
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
print(json.dumps({"rows":len(rows),"output":OUT,"mapping":"league+date+normalized_teams+final_score","model":"V4 frozen current code","t12h":True},ensure_ascii=False))
