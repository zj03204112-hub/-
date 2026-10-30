import csv,json,time
from pathlib import Path
from datetime import datetime,timezone
import requests
BASE="https://understat.com/"
HEADERS={"User-Agent":"Mozilla/5.0","X-Requested-With":"XMLHttpRequest","Referer":BASE,"Accept":"application/json, text/javascript, */*; q=0.01"}
CACHE=Path("data/season_match_detail_cache/understat"); OUT=Path("data/season_match_detail")
CACHE.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
def get(path,name):
 p=CACHE/name
 if p.exists(): return json.loads(p.read_text(encoding="utf-8"))
 r=requests.get(BASE+path,headers=HEADERS,timeout=45); r.raise_for_status(); d=r.json()
 p.write_text(json.dumps(d,ensure_ascii=False),encoding="utf-8"); time.sleep(1.1); return d
def norm(s): return "".join(c.lower() for c in (s or "") if c.isalnum())
def num(x):
 try: return float(x) if x not in (None,"") else None
 except (ValueError,TypeError): return None
src=OUT/"premier-league_2025_openfootball_results.csv"
with src.open(encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
league=get("getLeagueData/EPL/2025","EPL_2025_league.json"); dates=league.get("dates",[])
idx={}
for m in dates:
 h=m.get("h") or {}; a=m.get("a") or {}
 dt=(m.get("datetime") or m.get("date") or "")[:10]
 idx[(dt,norm(h.get("title")),norm(a.get("title")))]=m
errors=[]
for i,r in enumerate(rows,1):
 m=idx.get((r.get("date","")[:10],norm(r.get("home_team")),norm(r.get("away_team"))))
 r.update({"understat_match_id":"","understat_home_team":"","understat_away_team":"","home_xg_understat":"","away_xg_understat":"","home_xga_understat":"","away_xga_understat":"","home_roster_understat":"","away_roster_understat":"","understat_match_status":"unmatched_date_or_team_alias"})
 if not m: continue
 mid=str(m.get("id") or m.get("match_id") or "")
 r.update({"understat_match_id":mid,"understat_home_team":(m.get("h") or {}).get("title",""),"understat_away_team":(m.get("a") or {}).get("title",""),"understat_match_status":"matched_pending_detail"})
 if not mid: continue
 try:
  d=get("getMatchData/"+mid,"match_"+mid+".json"); shots=d.get("shots") or {}
  vals=[]
  for side in ("h","a"):
   xs=[num(s.get("xG")) for s in (shots.get(side) or []) if isinstance(s,dict)]
   vals.append(sum(x for x in xs if x is not None) if any(x is not None for x in xs) else None)
  r["home_xg_understat"]=round(vals[0],4) if vals[0] is not None else ""
  r["away_xg_understat"]=round(vals[1],4) if vals[1] is not None else ""
  r["home_xga_understat"]=r["away_xg_understat"]; r["away_xga_understat"]=r["home_xg_understat"]
  ro=d.get("rosters") or {}; r["home_roster_understat"]=json.dumps(ro.get("h"),ensure_ascii=False) if ro.get("h") is not None else ""
  r["away_roster_understat"]=json.dumps(ro.get("a"),ensure_ascii=False) if ro.get("a") is not None else ""
  r["understat_match_status"]="ok"
 except Exception as e:
  r["understat_match_status"]="detail_fetch_error"; errors.append({"match_id":mid,"error":str(e)})
 if i%25==0: print(f"Processed {i}/{len(rows)}",flush=True)
out=OUT/"premier-league_2025_enriched.csv"
cols=list(dict.fromkeys(k for r in rows for k in r))
with out.open("w",encoding="utf-8-sig",newline="") as f:
 w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(rows)
def has(r,k): return r.get(k) not in (None,"","null")
n=len(rows); matched=sum(has(r,"understat_match_id") for r in rows)
xg=sum(has(r,"home_xg_understat") and has(r,"away_xg_understat") for r in rows)
ro=sum(has(r,"home_roster_understat") and has(r,"away_roster_understat") for r in rows)
report={"source":"Understat free public data","source_docs":"https://github.com/withqwerty/football-docs/blob/main/docs/free-sources/understat.md","season":"2025-26","collected_at_utc":datetime.now(timezone.utc).isoformat(),"baseline_rows":n,"understat_league_match_records":len(dates),"matched_rows":matched,"match_id_coverage_pct":round(matched*100/n,2) if n else 0,"both_team_xg_rows":xg,"both_team_xg_coverage_pct":round(xg*100/n,2) if n else 0,"both_raw_roster_rows":ro,"both_raw_roster_coverage_pct":round(ro*100/n,2) if n else 0,"verified_starting_xi_rows":0,"verified_actual_appearance_rows":0,"notes":["xG is summed from shot-level xG; xGA is opponent xG.","Raw rosters are retained but not assumed to equal starters or confirmed appearances.","Unmatched rows remain blank; no values are imputed.","Understat match IDs are source-specific."],"fetch_errors":errors}
(OUT/"premier-league_2025_enriched_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
if matched==0: raise SystemExit("No matches joined; audit failed")
