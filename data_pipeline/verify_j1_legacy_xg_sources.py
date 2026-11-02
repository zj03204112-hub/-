#!/usr/bin/env python3
"""Strictly re-fetch archived free J1 match xG pages; never use team-season pages or impute."""
import csv,json,re,unicodedata
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
R=Path("data/season_match_detail"); MAP=R/"j1_2026_match_id_mapping.csv"; OUT=R/"j1_2026_match_xg_free.csv"
LEG=R/"j1_2026_match_xg_legacy_unverified.csv"; AUD=R/"j1_2026_match_xg_legacy_source_reverification_audit.json"; CAND=R/"j1_2026_match_xg_legacy_reverification_candidates.csv"
H={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"}
ALIASES={"横浜FM":["Yokohama F. Marinos","Yokohama Marinos"],"鹿島":["Kashima Antlers","Kashima"],"Ｇ大阪":["Gamba Osaka"],"G大阪":["Gamba Osaka"],"Ｃ大阪":["Cerezo Osaka"],"C大阪":["Cerezo Osaka"],"浦和":["Urawa Reds","Urawa Red Diamonds","Urawa"],"岡山":["Fagiano Okayama"],"名古屋":["Nagoya Grampus"],"清水":["Shimizu S-Pulse","Shimizu S Pulse"],"福岡":["Avispa Fukuoka"],"神戸":["Vissel Kobe"],"柏":["Kashiwa Reysol"],"水戸":["Mito Hollyhock"],"FC東京":["FC Tokyo"],"町田":["Machida Zelvia","FC Machida Zelvia"],"広島":["Sanfrecce Hiroshima"],"千葉":["JEF United Chiba","JEF United Ichihara Chiba","JEF United"],"東京Ｖ":["Tokyo Verdy"],"東京V":["Tokyo Verdy"],"川崎Ｆ":["Kawasaki Frontale"],"川崎F":["Kawasaki Frontale"],"長崎":["V-Varen Nagasaki","V Varen Nagasaki"],"京都":["Kyoto Sanga","Kyoto Sanga FC"]}
def ck(s): return re.sub(r"[^a-z0-9]+","",unicodedata.normalize("NFKC",str(s or "")).casefold())
def read(p):
 with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(p,rows,fields):
 with p.open("w",encoding="utf-8-sig",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)
def match_url(url):
 p=urlparse(url);path=p.path.lower()
 return (p.netloc.endswith("tifolab.com") and "/matches/" in path) or (p.netloc.endswith("vjstats.com") and "/matches/" in path) or (p.netloc.endswith("scoutingstats.ai") and "/j1-league/" in path and bool(re.search(r"/\d+/?$",path))) or (p.netloc.endswith("fotmob.com") and "/matches/" in path and bool(re.search(r"/[a-z0-9]+/?$",path)))
def verify(r):
 u=r.get("source_url",""); out={"internal_match_id":r.get("internal_match_id",""),"source_name":r.get("source_name",""),"source_url":u,"status":"","detail":"","current_home_xg":"","current_away_xg":"","retrieved_at_utc":datetime.now(timezone.utc).isoformat()}
 if not match_url(u):out.update(status="not_match_specific_page",detail="Team/league page is not accepted as match-level evidence.");return out
 try:
  res=requests.get(u,headers=H,timeout=25);res.raise_for_status();soup=BeautifulSoup(res.text,"html.parser");lines=[re.sub(r"\s+"," ",x).strip() for x in soup.get_text("\n",strip=True).splitlines()];lines=[x for x in lines if x];txt="\n".join(lines)
 except Exception as e:out.update(status="fetch_failed",detail=f"{type(e).__name__}: {str(e)[:180]}");return out
 try:d=date.fromisoformat(r["match_date"])
 except Exception:out.update(status="bad_date",detail=r.get("match_date",""));return out
 date_ok=any(t.casefold() in txt.casefold() for t in [d.isoformat(),f"{d.day} {d.strftime('%B %Y')}",f"{d.day} {d.strftime('%b %Y')}",f"{d.day} {d.strftime('%B')}",f"{d.day} {d.strftime('%b')}"])
 hg,ag=int(float(r["home_goals"])),int(float(r["away_goals"]))
 score_ok=bool(re.search(rf"(?<!\d){hg}\s*[–—-]\s*{ag}(?!\d)",txt))
 normtxt=ck(txt)
 def teamok(team):return any(ck(a) in normtxt for a in ALIASES.get(team,[team]))
 home_ok,away_ok=teamok(r.get("home_team","")),teamok(r.get("away_team",""))
 pairs=[]
 for i,line in enumerate(lines):
  if re.search(r"xG|expected goals",line,re.I):
   win=" ".join(lines[i:i+5])
   for m in re.finditer(r"(?i)(?:xg(?:\s*\([^)]*\))?|expected goals(?:\s*\(xg\))?)\s*(?:[: ]+)?(\d+\.\d+)\s*(?:to|[–—-])\s*(\d+\.\d+)",win):pairs.append((float(m.group(1)),float(m.group(2))))
   nums=[]
   for x in lines[i+1:i+5]:
    if re.fullmatch(r"\d+\.\d+",x):nums.append(float(x))
    elif nums:break
   if len(nums)>=2:pairs.append((nums[0],nums[1]))
 pairs=list(dict.fromkeys(pairs))
 if not all([date_ok,score_ok,home_ok,away_ok]):
  out.update(status="identity_check_failed",detail=f"date={date_ok};score={score_ok};home={home_ok};away={away_ok}");return out
 oldh=float(r["home_xg"]);olda=float(r["away_xg"]);good=[p for p in pairs if abs(p[0]-oldh)<0.011 and abs(p[1]-olda)<0.011]
 if len(good)!=1:out.update(status="xg_mismatch_or_ambiguous",detail=f"page pairs={pairs}; archived={oldh}-{olda}");return out
 out.update(status="verified_current_match_page",detail="Current public match page confirms date, home/away teams, score and archived xG.",current_home_xg=f"{good[0][0]:.3f}",current_away_xg=f"{good[0][1]:.3f}")
 return out
def main():
 mapping=read(MAP);canon=read(OUT);legacy=read(LEG);bycanon={x["internal_match_id"]:x for x in canon}
 todo=[r for r in legacy if not (bycanon.get(r["internal_match_id"],{}).get("home_xg") and bycanon.get(r["internal_match_id"],{}).get("away_xg") and bycanon.get(r["internal_match_id"],{}).get("source_validation_status")=="verified_from_current_fetch")]
 results=[]
 with ThreadPoolExecutor(max_workers=5) as ex:
  fs=[ex.submit(verify,r) for r in todo]
  for i,f in enumerate(as_completed(fs),1):
   results.append(f.result())
   if i%10==0:print(f"Rechecked {i}/{len(todo)} legacy URLs",flush=True)
 legby={x["internal_match_id"]:x for x in legacy}; checks={x["internal_match_id"]:x for x in results}; now=datetime.now(timezone.utc).isoformat();accepted=0
 fields=list(canon[0].keys()) if canon else []
 for k in ["source_validation_status","source_reverification_note","source_raw_row","retrieved_at_utc"]:
  if k not in fields:fields.append(k)
 for row in canon:
  mid=row["internal_match_id"];check=checks.get(mid);old=legby.get(mid)
  if not old or not check or check["status"]!="verified_current_match_page":continue
  if row.get("source_validation_status")=="verified_from_current_fetch" and row.get("home_xg") and row.get("away_xg"):continue
  row.update(home_xg=check["current_home_xg"],away_xg=check["current_away_xg"],home_xga=check["current_away_xg"],away_xga=check["current_home_xg"],xg_status="published_on_free_public_source",source_name=old["source_name"],source_url=old["source_url"],source_model_note=f'{old["source_name"]} provider-specific match xG verified from current public match page; not official J.League xG. xGA is opponent-xG-equivalent, not independently published.',source_validation_status="verified_from_current_legacy_source_fetch",source_reverification_note=check["detail"],source_raw_row=old.get("source_raw_row",""),retrieved_at_utc=check["retrieved_at_utc"]);accepted+=1
 write(OUT,canon,fields);write(CAND,results,["internal_match_id","source_name","source_url","status","detail","current_home_xg","current_away_xg","retrieved_at_utc"])
 counts={}
 for r in results:counts[r["status"]]=counts.get(r["status"],0)+1
 valid=sum(bool(r.get("home_xg") and r.get("away_xg") and str(r.get("source_validation_status","")).startswith("verified_from_current")) for r in canon)
 audit={"generated_at_utc":now,"mapped_matches":len(mapping),"legacy_rows_checked":len(results),"legacy_rows_accepted_this_run":accepted,"canonical_rows_with_currently_verified_xg":valid,"canonical_rows_missing_verified_xg":len(mapping)-valid,"source_status_counts":counts,"independent_match_level_xga_verified":0,"xga_semantics":"opponent-xG-equivalent only; no independent match-level xGA was verified","policy":"free public match pages only; strict date/team/score/xG match; no team-season page, imputation or cross-provider averaging","candidate_file":CAND.name}
 AUD.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
