#!/usr/bin/env python3
"""Audit SGOdds opening Asian handicap coverage against the frozen 448-match sample."""
import csv, io, json, re, unicodedata
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/"model_validation/v52_jincai_20260901_20261013/v52_plus_nations_league_448_merged_backtest_20261013.csv"
OUT=ROOT/"model_validation/v52_jincai_20260901_20261013"
PAGE="https://sgodds.com/football/data"
LEAGUES=["English Premier","French League","German League","Italian League","Spanish League","J League","K League"]
ALIASES={"横浜fm":"横滨f水手","横浜fマリノス":"横滨f水手","横滨水手":"横滨f水手","c大阪":"大阪樱花","ｃ大阪":"大阪樱花","东京v":"东京verdy","东京绿茵":"东京verdy","fc东京":"东京fc","东京fc":"东京fc","川崎f":"川崎前锋","清水":"清水心跳","広島":"广岛三箭","名古屋":"名古屋鲸八","柏":"柏太阳神","鹿島":"鹿岛鹿角","水戸":"水户蜀葵","京都":"京都不死鸟","千葉":"千叶市原","岡山":"冈山绿雉","町田":"町田泽维亚","神戸":"神户胜利船","長崎":"长崎成功丸","浦和":"浦和红钻","福岡":"福冈黄蜂"}
class Links(HTMLParser):
 def __init__(self):
  super().__init__(); self.hrefs=[]
 def handle_starttag(self,tag,attrs):
  if tag.lower()=="a":
   d=dict(attrs); h=d.get("href","")
   if "downloads/" in h and ".csv" in h.lower(): self.hrefs.append(h)
def norm(v):
 v=unicodedata.normalize("NFKC",str(v or "")).lower().strip()
 v=re.sub(r"[^0-9a-z\u4e00-\u9fff]","",v)
 return ALIASES.get(v,v)
def parse_date(v):
 for fmt in ("%Y-%m-%d","%d/%m/%Y","%d/%m/%y","%Y/%m/%d","%m/%d/%Y"):
  try:return datetime.strptime(str(v or "").strip(),fmt).strftime("%Y-%m-%d")
  except ValueError:pass
 return ""
def find_col(cols,names):
 canon=lambda x:re.sub(r"[^a-z0-9]","",str(x).lower())
 for c in cols:
  if canon(c) in {canon(n) for n in names}:return c
 return None
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 with SAMPLE.open(encoding="utf-8-sig",newline="") as f: sample=list(csv.DictReader(f))
 if len(sample)!=448: raise SystemExit("FROZEN_SAMPLE_FAIL expected 448 got "+str(len(sample)))
 s=requests.Session();s.headers.update({"User-Agent":"Mozilla/5.0","Referer":PAGE})
 page=s.get(PAGE,timeout=30);page.raise_for_status()
 parser=Links();parser.feed(page.text)
 downloads={}
 for href in parser.hrefs:
  full=href if href.startswith("http") else "https://sgodds.com"+href
  base=full.rsplit("/",1)[-1].lower()
  for league in LEAGUES:
   slug=league.lower().replace(" ","-")
   if slug in base:downloads[league]=full
 reports=[]; records=[]; counts=Counter()
 for league,url in downloads.items():
  try:
   response=s.get(url,timeout=45);response.raise_for_status()
   text=response.content.decode("utf-8-sig",errors="replace")
   rows=list(csv.DictReader(io.StringIO(text)))
   if not rows:
    reports.append({"league":league,"url":url,"status":"EMPTY","rows":0});counts["empty_files"]+=1;continue
   cols=list(rows[0].keys())
   dc=find_col(cols,["date","matchdate","game_date"])
   hc=find_col(cols,["home","hometeam","home_team"])
   ac=find_col(cols,["away","awayteam","away_team"])
   lc=find_col(cols,["ah_01_hcap","ah01hcap","ahhcap","asianhandicap","ahcap","handicap"])
   if not all([dc,hc,ac,lc]):
    reports.append({"league":league,"url":url,"status":"MISSING_COLUMNS","rows":len(rows),"columns":cols[:35]});counts["bad_columns"]+=1;continue
   index={}
   for row in rows:
    d=parse_date(row.get(dc))
    if d:index.setdefault((d,norm(row.get(hc)),norm(row.get(ac))),[]).append(row)
   hit=0
   for match in sample:
    if match.get("sample_source")=="NationsLeague_V5":continue
    key=(match["date"],norm(match["home_team"]),norm(match["away_team"]))
    candidates=index.get(key,[])
    if len(candidates)!=1:
     # Conservative aliases only; do not fuzzy-match ambiguous team names.
     continue
    row=candidates[0];line=str(row.get(lc,"")).strip()
    if not line or line.lower() in ("nan","none","-"):continue
    records.append({"match_id":match["match_id"],"date":match["date"],"home_team":match["home_team"],"away_team":match["away_team"],"league_source":league,"market":"ASIAN_HANDICAP_OPEN","handicap_line":line,"source_url":url,"timestamp":"","time_quality":"OPENING_SNAPSHOT_NO_EXACT_TIMESTAMP"})
    hit+=1
   reports.append({"league":league,"url":url,"status":"OK","source_rows":len(rows),"dated_team_keys":len(index),"matched_sample_rows":hit})
   counts["matched_rows"]+=hit
  except Exception as e:
   reports.append({"league":league,"url":url,"status":"ERROR","error":str(e)[:180]});counts["fetch_errors"]+=1
 unique={}
 for row in records:unique.setdefault(row["match_id"],row)
 coverage=[]
 for match in sample:
  row=unique.get(match["match_id"])
  coverage.append({"match_id":match["match_id"],"date":match["date"],"home_team":match["home_team"],"away_team":match["away_team"],"mapping_status":"MATCHED_OPENING_ONLY" if row else "NO_VERIFIED_SGODDS_MATCH","source":row["league_source"] if row else "","handicap_line":row["handicap_line"] if row else "","time_quality":row["time_quality"] if row else ""})
 def write(path,rows):
  keys=list(dict.fromkeys(k for row in rows for k in row))
  with path.open("w",encoding="utf-8-sig",newline="") as f:
   w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
 write(OUT/"v52_448_sgodds_opening_handicap.csv",records)
 write(OUT/"v52_448_sgodds_match_coverage.csv",coverage)
 report={"sample_size":448,"source_files":reports,"rows_collected":len(records),"unique_matches_with_opening_handicap":len(unique),"matches_without_verified_opening_handicap":448-len(unique),"timestamped_rows":0,"strict_T_minus_12_eligible_matches":0,"status_counts":dict(counts),"note":"Opening snapshot records have no exact intraday timestamp and are not T-12 eligible. Audit does not modify database or model."}
 (OUT/"v52_448_sgodds_coverage_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"source_file_diagnostics":reports},ensure_ascii=False),flush=True)
 print(json.dumps({"sample_size":448,"source_files_found":len(downloads),"rows_collected":len(records),"unique_matches_with_opening_handicap":len(unique),"matches_without_verified_opening_handicap":448-len(unique),"timestamped_rows":0,"strict_T_minus_12_eligible_matches":0,"counts":dict(counts)},ensure_ascii=False),flush=True)
if __name__=="__main__":main()
