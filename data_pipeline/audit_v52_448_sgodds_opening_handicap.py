#!/usr/bin/env python3
"""Audit SGOdds opening Asian-handicap CSV coverage against frozen 448 sample.
No database/model mutation; no invented timestamps; report unique team/date matches.\nThis collector is run via the dedicated SGOdds coverage workflow.
"""
import csv, io, json, re, unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/"model_validation/v52_jincai_20260901_20261013/v52_plus_nations_league_448_merged_backtest_20261013.csv"
OUT=ROOT/"model_validation/v52_jincai_20260901_20261013"
PAGE="https://sgodds.com/football/data"
LEAGUES={
 "English Premier":"EPL","French League":"LIGUE1","German League":"BUNDESLIGA",
 "Italian League":"SERIEA","Spanish League":"LALIGA","J League":"J1","K League":"KLEAGUE1"
}
ALIASES={
 "横浜fm":"横滨f水手","横浜fマリノス":"横滨f水手","横滨水手":"横滨f水手",
 "c大阪":"大阪樱花","ｃ大阪":"大阪樱花","东京v":"东京verdy","东京绿茵":"东京verdy",
 "fc东京":"东京fc","东京fc":"东京fc","川崎f":"川崎前锋","川崎前锋":"川崎前锋",
 "大阪钢巴":"大阪钢巴","清水":"清水心跳","清水心跳":"清水心跳",
 "広島":"广岛三箭","名古屋":"名古屋鲸八","柏":"柏太阳神","鹿島":"鹿岛鹿角",
 "水戸":"水户蜀葵","京都":"京都不死鸟","千葉":"千叶市原","岡山":"冈山绿雉",
 "町田":"町田泽维亚","神戸":"神户胜利船","長崎":"长崎成功丸","浦和":"浦和红钻",
 "福岡":"福冈黄蜂","札幌":"北海道札幌冈萨多","仙台":"仙台七夕"
}
def norm(v):
 v=unicodedata.normalize("NFKC",str(v or "")).lower().strip()
 v=re.sub(r"[^0-9a-z\u4e00-\u9fff]","",v)
 return ALIASES.get(v,v)
def dateparse(v):
 v=str(v or "").strip()
 for f in ("%Y-%m-%d","%d/%m/%Y","%d/%m/%y","%Y/%m/%d","%m/%d/%Y"):
  try:return datetime.strptime(v,f).strftime("%Y-%m-%d")
  except ValueError:pass
 return ""
def pick(cols,*names):
 for c in cols:
  cc=re.sub(r"[^a-z0-9]","",c.lower())
  if cc in {re.sub(r"[^a-z0-9]","",n.lower()) for n in names}:return c
 for c in cols:
  cc=re.sub(r"[^a-z0-9]","",c.lower())
  if any(re.sub(r"[^a-z0-9]","",n.lower()) in cc for n in names):return c
 return None
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 with SAMPLE.open(encoding="utf-8-sig",newline="") as f: sample=list(csv.DictReader(f))
 if len(sample)!=448: raise SystemExit(f"FROZEN_SAMPLE_FAIL expected 448 got {len(sample)}")
 s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0","Referer":PAGE})
 html=s.get(PAGE,timeout=30);html.raise_for_status()
 links=re.findall(r"href=\"([^\"]*downloads/[^\"]+\\.csv)\"",html.text,re.I)
  downloads={}
  for href in links:
   full=href if href.startswith("http") else "https://sgodds.com"+href
   base=full.rsplit("/",1)[-1].lower()
   for league in LEAGUES:
    slug=league.lower().replace(" ","-")
    if slug in base: downloads[league]=full
  results=[]; status=Counter(); file_reports=[]
 for league,url in downloads.items():
  try:
   resp=s.get(url,timeout=45);resp.raise_for_status()
   txt=resp.content.decode("utf-8-sig",errors="replace")
   rows=list(csv.DictReader(io.StringIO(txt)))
   if not rows:status["empty_"+league]+=1;continue
   cols=list(rows[0].keys())
   dc=pick(cols,"date","matchdate","game_date")
   hc=pick(cols,"home","hometeam","home_team")
   ac=pick(cols,"away","awayteam","away_team")
   linec=pick(cols,"ah_01_hcap","ah01hcap","ahhcap","asianhandicap","ahcap","handicap")
   if not all([dc,hc,ac,linec]):
    file_reports.append({"league":league,"url":url,"rows":len(rows),"columns":cols[:40],"status":"MISSING_REQUIRED_COLUMNS"})
    status["bad_columns_"+league]+=1;continue
   indexed={}
   for q in rows:
    d=dateparse(q.get(dc))
    if not d:continue
    key=(d,norm(q.get(hc)),norm(q.get(ac)))
    indexed.setdefault(key,[]).append(q)
   hits=0
   for r in sample:
    d=r["date"]; home=norm(r["home_team"]); away=norm(r["away_team"])
    if r.get("sample_source")=="NationsLeague_V5":continue
    matches=[]
    for (qd,qh,qa),qs in indexed.items():
     if qd==d and qh and qa and ((qh==home and qa==away) or (qh in home and len(qh)>3 and qa in away and len(qa)>3) or (home in qh and len(home)>3 and away in qa and len(away)>3)):
      matches.extend(qs)
    if len(matches)!=1:continue
    q=matches[0]; line=str(q.get(linec,"")).strip()
    if not line or line.lower() in ("nan","none","-"):continue
    results.append({"match_id":r["match_id"],"date":d,"home_team":r["home_team"],"away_team":r["away_team"],
      "league_source":league,"market":"ASIAN_HANDICAP_OPEN","handicap_line":line,
      "home_odds":q.get(pick(cols,"ah_01_home","ah01home","ahhome","homeodds","ahh"),""),
      "away_odds":q.get(pick(cols,"ah_01_away","ah01away","ahaway","awayodds","aha"),""),
      "source_url":url,"timestamp":"","time_quality":"OPENING_SNAPSHOT_NO_EXACT_TIMESTAMP"})
    hits+=1
   file_reports.append({"league":league,"url":url,"rows":len(rows),"indexed_date_team_keys":len(indexed),"sample_matches_with_handicap":hits,"status":"OK"})
   status["matched_rows"]+=hits
  except Exception as e:
   status["fetch_error_"+league]+=1;file_reports.append({"league":league,"url":url,"status":"ERROR","error":str(e)[:200]})
 # one unique per match; keep source variants separate in audit and report unique coverage
 unique={}
 for q in results: unique.setdefault(q["match_id"],q)
 matched=set(unique)
 mapping=[]
 for r in sample:
  q=unique.get(r["match_id"])
  mapping.append({"match_id":r["match_id"],"date":r["date"],"home_team":r["home_team"],"away_team":r["away_team"],
    "mapping_status":"MATCHED_OPENING_ONLY" if q else "NO_VERIFIED_SGODDS_MATCH",
    "source":q["league_source"] if q else "","handicap_line":q["handicap_line"] if q else "",
    "time_quality":q["time_quality"] if q else ""})
 def write(path,rows):
  cols=list(dict.fromkeys(k for x in rows for k in x))
  with path.open("w",encoding="utf-8-sig",newline="") as f:
   w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
 write(OUT/"v52_448_sgodds_opening_handicap.csv",results)
 write(OUT/"v52_448_sgodds_match_coverage.csv",mapping)
 report={"sample_size":448,"downloaded_leagues":downloads,"source_files":file_reports,
  "rows_collected":len(results),"unique_matches_with_opening_handicap":len(matched),
  "matches_without_verified_opening_handicap":448-len(matched),
  "timestamped_rows":0,"strict_T_minus_12_eligible_matches":0,
  "status_counts":dict(status),
  "important_note":"SGOdds dataset is opening-odds snapshot and does not provide an exact intraday timestamp in these CSV rows. It must not be treated as T-12. This audit does not modify the database or V5.2."}
 (OUT/"v52_448_sgodds_coverage_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"sample_size":448,"downloaded_leagues":list(downloads),"rows_collected":len(results),"unique_matches_with_opening_handicap":len(matched),"matches_without_verified_opening_handicap":448-len(matched),"timestamped_rows":0,"strict_T_minus_12_eligible_matches":0,"status_counts":dict(status)},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
