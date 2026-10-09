import csv, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"football_model_database.sqlite"
OUT=ROOT/"model_validation"/"database_audit"
OUT.mkdir(parents=True,exist_ok=True)
EXPECTED=["matches","competitions","seasons","teams","results","sporttery_market","team_features","schedule_intent","injuries","prediction_snapshot","lineup_projection","provider_event_map","player_match_stats","player_features","review","advanced_team_features","schema_migrations"]
def qi(s): return '"'+s.replace('"','""')+'"'
def cols(c,t): return [r[1] for r in c.execute("PRAGMA table_info("+qi(t)+")")]
def first(cs,opts): return next((x for x in opts if x in cs),None)
def count(c,t): return c.execute("SELECT COUNT(*) FROM "+qi(t)).fetchone()[0]
R={"report":"Football model database audit","generated_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds"),"database_path":"football_model_database.sqlite","database_file_exists":DB.exists(),"database_size_bytes":DB.stat().st_size if DB.exists() else None,"audit_status":"NOT_RUN_DATABASE_FILE_MISSING" if not DB.exists() else "RUNNING","tables":[],"league_coverage":[],"match_coverage":{},"module_coverage":[],"player_data_coverage":{},"source_coverage":[],"integrity_checks":{},"mapping_files":[],"limitations":[]}
if DB.exists():
 try:
  c=sqlite3.connect("file:"+str(DB)+"?mode=ro",uri=True); c.row_factory=sqlite3.Row
  names=[x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]; ns=set(names)
  R["tables"]=[{"table":t,"row_count":count(c,t),"columns":cols(c,t)} for t in names]
  R["missing_expected_tables"]=[t for t in EXPECTED if t not in ns]
  if "matches" in ns:
   mc=cols(c,"matches"); idc=first(mc,["match_id","id"]); dc=first(mc,["kickoff","match_date","date"]); sc=first(mc,["status"]); lc=first(mc,["competition_id","league","competition"]); hc=first(mc,["home_team","home","home_name"]); ac=first(mc,["away_team","away","away_name"])
   m={"total_matches":count(c,"matches")}
   if sc: m["status_counts"]={str(x[0] if x[0] is not None else "NULL"):x[1] for x in c.execute("SELECT "+qi(sc)+",COUNT(*) FROM matches GROUP BY "+qi(sc))}
   if dc: m["earliest_kickoff"],m["latest_kickoff"]=c.execute("SELECT MIN("+qi(dc)+"),MAX("+qi(dc)+") FROM matches").fetchone()
   if idc:
    m["distinct_match_ids"]=c.execute("SELECT COUNT(DISTINCT "+qi(idc)+") FROM matches").fetchone()[0]
    m["duplicate_match_id_rows"]=m["total_matches"]-m["distinct_match_ids"]
    m["null_match_id_rows"]=c.execute("SELECT COUNT(*) FROM matches WHERE "+qi(idc)+" IS NULL OR TRIM(CAST("+qi(idc)+" AS TEXT))=''").fetchone()[0]
   for k,col in [("null_home_team_rows",hc),("null_away_team_rows",ac)]:
    if col: m[k]=c.execute("SELECT COUNT(*) FROM matches WHERE "+qi(col)+" IS NULL OR TRIM(CAST("+qi(col)+" AS TEXT))=''").fetchone()[0]
   if "results" in ns and idc:
    rc=cols(c,"results"); rid=first(rc,["match_id"]); hg=first(rc,["ft_home","home_goals","home_score","goals_home"]); ag=first(rc,["ft_away","away_goals","away_score","goals_away"])
    if rid:
     m["result_rows"]=count(c,"results"); m["matches_with_result"]=c.execute("SELECT COUNT(DISTINCT "+qi(rid)+") FROM results").fetchone()[0]
     if hg and ag: m["rows_with_final_score"]=c.execute("SELECT COUNT(*) FROM results WHERE "+qi(hg)+" IS NOT NULL AND "+qi(ag)+" IS NOT NULL").fetchone()[0]
   R["match_coverage"]=m
   if lc and lc=="competition_id" and "competitions" in ns:
    cc=cols(c,"competitions"); cid=first(cc,["competition_id","id"]); name=first(cc,["name","competition_name","league_name","short_name"])
    if cid and name and dc:
     R["league_coverage"]=[dict(x) for x in c.execute("SELECT c."+qi(name)+" AS league,COUNT(*) AS n,MIN(m."+qi(dc)+") AS first_match,MAX(m."+qi(dc)+") AS last_match FROM matches m LEFT JOIN competitions c ON m."+qi(lc)+"=c."+qi(cid)+" GROUP BY c."+qi(name)+" ORDER BY n DESC")]
    else: R["league_coverage"]=[{"league":str(x[0]),"matches":x[1]} for x in c.execute("SELECT "+qi(lc)+",COUNT(*) FROM matches GROUP BY "+qi(lc)+" ORDER BY COUNT(*) DESC")]
   elif lc: R["league_coverage"]=[{"league":str(x[0]),"matches":x[1]} for x in c.execute("SELECT "+qi(lc)+",COUNT(*) FROM matches GROUP BY "+qi(lc)+" ORDER BY COUNT(*) DESC")]
   for t in ["results","sporttery_market","team_features","schedule_intent","injuries","prediction_snapshot","lineup_projection","provider_event_map","player_match_stats","player_features","review","advanced_team_features"]:
    if t not in ns: R["module_coverage"].append({"table":t,"exists":False,"rows":None,"distinct_match_ids":None,"coverage_pct":None}); continue
    tc=cols(c,t); fk=first(tc,["match_id"]); n=count(c,t); d=c.execute("SELECT COUNT(DISTINCT match_id) FROM "+qi(t)+" WHERE match_id IS NOT NULL").fetchone()[0] if fk else None; total=m["total_matches"]
    R["module_coverage"].append({"table":t,"exists":True,"rows":n,"distinct_match_ids":d,"coverage_pct":round(100*d/total,2) if d is not None and total else None})
  else: R["limitations"].append("No matches table found.")
  if "player_match_stats" in ns:
   pc=cols(c,"player_match_stats"); p={"rows":count(c,"player_match_stats")}
   for k,col in [("distinct_matches","match_id"),("distinct_players","player_id")]:
    if col in pc: p[k]=c.execute("SELECT COUNT(DISTINCT "+qi(col)+") FROM player_match_stats").fetchone()[0]
   for metric in ["xg","xa","rating","minutes_played","starter"]:
    if metric in pc:
     p[metric+"_non_null_rows"]=c.execute("SELECT COUNT(*) FROM player_match_stats WHERE "+qi(metric)+" IS NOT NULL").fetchone()[0]
     if metric in ["xg","xa","rating"]: p[metric+"_positive_rows"]=c.execute("SELECT COUNT(*) FROM player_match_stats WHERE "+qi(metric)+">0").fetchone()[0]
   R["player_data_coverage"]=p
   if "data_source" in pc: R["source_coverage"]=[{"source":str(x[0]),"rows":x[1],"distinct_matches":x[2]} for x in c.execute("SELECT data_source,COUNT(*),COUNT(DISTINCT match_id) FROM player_match_stats GROUP BY data_source ORDER BY COUNT(*) DESC")]
  for p in ROOT.rglob("*mapping*.csv"):
   if p.is_file() and ".git" not in p.parts: R["mapping_files"].append({"path":str(p.relative_to(ROOT)),"size_bytes":p.stat().st_size})
  R["audit_status"]="COMPLETED"; c.close()
 except Exception as e: R["audit_status"]="FAILED"; R["error"]=repr(e)
else: R["limitations"].append("SQLite database file is absent from this checkout. No row counts are fabricated; run audit where the real DB is available.")
(OUT/"database_audit.json").write_text(json.dumps(R,ensure_ascii=False,indent=2),encoding="utf-8")
with (OUT/"database_tables.csv").open("w",newline="",encoding="utf-8-sig") as f:
 w=csv.DictWriter(f,fieldnames=["table","row_count","columns"]); w.writeheader()
 for x in R.get("tables",[]): w.writerow({"table":x["table"],"row_count":x["row_count"],"columns":";".join(x["columns"])})
L=["# Football model database audit","", "- Generated UTC: "+R["generated_at_utc"],"- Database file exists: "+str(R["database_file_exists"]),"- File size bytes: "+str(R["database_size_bytes"]),"- Audit status: "+R["audit_status"],"", "This report counts only the SQLite file present in this GitHub Actions checkout. Schema definitions are not treated as evidence that data exists.",""]
if R.get("match_coverage"):
 L+=["## Match coverage","","| Metric | Actual value |","|---|---:|"]+["| "+str(k)+" | "+str(v)+" |" for k,v in R["match_coverage"].items()]
if R.get("league_coverage"):
 L+=["","## League/competition coverage","","| League | Matches | Earliest | Latest |","|---|---:|---|---|"]+["| "+str(x.get("league"))+" | "+str(x.get("n",x.get("matches","")))+" | "+str(x.get("first_match",""))+" | "+str(x.get("last_match",""))+" |" for x in R["league_coverage"]]
if R.get("tables"):
 L+=["","## Table inventory","","| Table | Rows | Columns |","|---|---:|---:|"]+["| "+x["table"]+" | "+str(x["row_count"])+" | "+str(len(x["columns"]))+" |" for x in R["tables"]]
if R.get("module_coverage"):
 L+=["","## Module coverage","","| Table | Exists | Rows | Distinct matches | Coverage |","|---|---|---:|---:|---:|"]+["| "+x["table"]+" | "+str(x["exists"])+" | "+str(x["rows"])+" | "+str(x["distinct_match_ids"])+" | "+(str(x["coverage_pct"])+"%" if x["coverage_pct"] is not None else "N/A")+" |" for x in R["module_coverage"]]
if R.get("player_data_coverage"): L+=["","## Player data coverage","",json.dumps(R["player_data_coverage"],ensure_ascii=False,indent=2)]
if R.get("source_coverage"): L+=["","## Player data sources","","| Source | Rows | Distinct matches |","|---|---:|---:|"]+["| "+x["source"]+" | "+str(x["rows"])+" | "+str(x["distinct_matches"])+" |" for x in R["source_coverage"]]
if R.get("missing_expected_tables"): L+=["","## Missing expected tables","",", ".join(R["missing_expected_tables"])]
if R.get("limitations"): L+=["","## Limitations",""]+["- "+x for x in R["limitations"]]
if R.get("error"): L+=["","## Error","",R["error"]]
(OUT/"database_audit.md").write_text("\n".join(L)+"\n",encoding="utf-8")
print(json.dumps({"audit_status":R["audit_status"],"database_file_exists":R["database_file_exists"],"database_size_bytes":R["database_size_bytes"],"table_count":len(R.get("tables",[])),"match_coverage":R.get("match_coverage",{}),"league_count":len(R.get("league_coverage",[])),"module_coverage":R.get("module_coverage",[]),"limitations":R.get("limitations",[]),"error":R.get("error")},ensure_ascii=False,indent=2))
