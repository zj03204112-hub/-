import csv, json, re, sqlite3
from collections import Counter

CSV_PATH = "model_validation/500_match_base_batch01.csv"
DB = "football_model_database.sqlite"
OUT = "model_validation/500_match_to_db_match_id_mapping.csv"

LEAGUE_CODE = {
    "英超":"EPL", "EPL":"EPL", "Premier League":"EPL",
    "西甲":"LALIGA", "La Liga":"LALIGA", "LaLiga":"LALIGA",
    "德甲":"BUNDESLIGA", "Bundesliga":"BUNDESLIGA",
    "意甲":"SERIEA", "Serie A":"SERIEA",
    "法甲":"LIGUE1", "Ligue 1":"LIGUE1",
    "韩职":"KLEAGUE1", "K League 1":"KLEAGUE1", "K1":"KLEAGUE1",
    "日职J1":"J1", "J1 League":"J1", "J1":"J1",
}

def norm_team(s):
    s=(s or "").strip().lower()
    for token in ("football club","footballclub","fc","cf","afc","sc","fk","ac","calcio","club","足球俱乐部"):
        s=s.replace(token,"")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+","",s)

con=sqlite3.connect(DB)
schema=con.execute("PRAGMA table_info(matches)").fetchall()
cols=[r[1] for r in schema]
required={"match_id","competition_id","kickoff","home_team","away_team"}
missing=required-set(cols)
if missing:
    raise RuntimeError(f"MATCHES_SCHEMA_MISSING {sorted(missing)}")

db_rows=con.execute("""
SELECT m.match_id,m.competition_id,c.competition_code,c.competition_name,
       m.kickoff,m.home_team,m.away_team,
       r.ft_home,r.ft_away
FROM matches m
JOIN competitions c ON c.competition_id=m.competition_id
JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished'
  AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
""").fetchall()

idx={}
for x in db_rows:
    key=(x[2],x[4][:10],norm_team(x[5]),norm_team(x[6]),x[7],x[8])
    idx.setdefault(key,[]).append(x)

out=[]
with open(CSV_PATH,encoding="utf-8-sig",newline="") as f:
    src=list(csv.DictReader(f))

for i,r in enumerate(src,1):
    league=r["league"].strip()
    code=LEAGUE_CODE.get(league)
    score=(int(r["home_score"]),int(r["away_score"]))
    key=(code,r["date"],norm_team(r["home"]),norm_team(r["away"]),score[0],score[1])
    cand=idx.get(key,[])
    status="unique" if len(cand)==1 else "unresolved" if len(cand)==0 else "ambiguous"
    x=cand[0] if len(cand)==1 else [None, None, code, None, None, None, None, None, None]
    out.append({
        "sample_row":i,"date":r["date"],"league":league,"competition_code":code,
        "home":r["home"],"away":r["away"],"home_score":score[0],"away_score":score[1],
        "mapping_status":status,"candidate_count":len(cand),
        "match_id":x[0] if len(cand)==1 else "",
        "db_kickoff":x[4] if len(cand)==1 else "",
        "db_home":x[5] if len(cand)==1 else "",
        "db_away":x[6] if len(cand)==1 else "",
        "db_home_score":x[7] if len(cand)==1 else "",
        "db_away_score":x[8] if len(cand)==1 else "",
    })

if len(out)!=500 or len({x["sample_row"] for x in out})!=500:
    raise RuntimeError(f"FROZEN_SAMPLE_INTEGRITY_FAILED rows={len(out)}")

fields=list(out[0])
with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)

counts=Counter(x["mapping_status"] for x in out)
print(json.dumps({"rows":len(out),"status_counts":counts,"output":OUT},ensure_ascii=False))
print("DB_FINISHED_RESULTS", con.execute("SELECT COUNT(*) FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL").fetchone()[0])
print("DB_DATE_RANGE", con.execute("SELECT MIN(kickoff),MAX(kickoff) FROM matches WHERE status='finished'").fetchone())
print("DB_COMPETITIONS", con.execute("SELECT c.competition_code,COUNT(*) FROM matches m JOIN competitions c ON c.competition_id=m.competition_id WHERE m.status='finished' GROUP BY c.competition_code ORDER BY c.competition_code").fetchall())
print("DB_SAMPLE", con.execute("SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away,c.competition_code FROM matches m JOIN results r ON r.match_id=m.match_id JOIN competitions c ON c.competition_id=m.competition_id WHERE m.status='finished' ORDER BY m.kickoff LIMIT 5").fetchall())
