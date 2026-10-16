#!/usr/bin/env python3
"""Append verified full-time Sporttery results to an existing V5.2 trace record."""
import hashlib, json, os, sqlite3, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TRACE=Path(os.getenv("V52_TRACE_DB",str(ROOT/"data"/"v52_live_trace.sqlite")))
BASE="https://webapi.sporttery.cn/gateway/uniform/fb/getMatchDataPageListV1.qry"
HEAD={"User-Agent":"Mozilla/5.0","Referer":"https://m.sporttery.cn/","Accept":"application/json,text/plain,*/*"}
def norm(s):
    import re
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]","",(s or "").lower())
def digest(b): return hashlib.sha256(b).hexdigest()
def get(day):
    url=BASE+"?"+urllib.parse.urlencode({"method":"result","matchDate":day})
    req=urllib.request.Request(url,headers=HEAD)
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read()
    j=json.loads(raw); rows=[m for g in ((j.get("value") or {}).get("matchInfoList") or []) for m in (g.get("subMatchList") or [])]
    return rows,url,raw
def score(m):
    import re
    xs=re.findall(r"\d+",str(m.get("sectionsNo999") or ""))
    if len(xs)<2:return None
    return int(xs[-2]),int(xs[-1])
def main():
    if not TRACE.exists(): raise SystemExit("TRACE_DB_NOT_FOUND")
    day=os.getenv("FIXTURE_DATE","").strip()
    if not day: raise SystemExit("FIXTURE_DATE_REQUIRED")
    rows,url,raw=get(day); retrieved=datetime.now(timezone.utc).isoformat(timespec="seconds")
    c=sqlite3.connect(TRACE)
    existing=c.execute("SELECT internal_match_id,provider_match_id,home,away FROM fixtures WHERE provider='sporttery'").fetchall()
    inserted=0; unmatched=[]; conflicts=[]
    for mid,provider_id,home,away in existing:
        candidates=[m for m in rows if str(m.get("matchId") or "")==str(provider_id)]
        if len(candidates)!=1:
            continue
        m=candidates[0]; hs,as_=score(m) or (None,None)
        if hs is None or as_ is None:
            continue
        mh=m.get("allHomeTeam") or m.get("homeTeam") or ""; ma=m.get("allAwayTeam") or m.get("awayTeam") or ""
        if norm(mh)!=norm(home) or norm(ma)!=norm(away):
            conflicts.append({"match_id":mid,"sporttery_match_id":provider_id,"expected":[home,away],"observed":[mh,ma]})
            continue
        raw_obj={"sporttery_match":m,"list_url":url,"list_response_sha256":digest(raw)}
        event_time=m.get("completedAt") or m.get("resultTime") or m.get("updateTime")
        try:
            c.execute("INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?,?)",
              (mid,"sporttery",str(provider_id),hs,as_,event_time,retrieved,url,
               json.dumps(raw_obj,ensure_ascii=False),digest(json.dumps(raw_obj,ensure_ascii=False,sort_keys=True).encode())))
            inserted+=1
        except sqlite3.IntegrityError:
            # Existing final result is immutable; compare exact values rather than overwrite.
            old=c.execute("SELECT ft_home,ft_away,provider_event_id FROM results WHERE internal_match_id=?",(mid,)).fetchone()
            if old!=(hs,as_,str(provider_id)):
                conflicts.append({"match_id":mid,"existing":old,"new":[hs,as_,str(provider_id)]})
    c.commit()
    report={"date":day,"provider":"Sporttery official public endpoint","retrieved_at":retrieved,
      "source_url":url,"source_payload_sha256":digest(raw),"new_results_appended":inserted,
      "identity_conflicts":conflicts,"status":"BLOCKED_IDENTITY_CONFLICT" if conflicts else "RESULT_SYNC_COMPLETE",
      "note":"No existing prediction or result row is overwritten."}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    c.close()
    if conflicts: raise SystemExit(2)
if __name__=="__main__":main()
