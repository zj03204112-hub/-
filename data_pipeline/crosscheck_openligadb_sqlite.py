#!/usr/bin/env python3
"""Cross-check a league-season against local SQLite by date, teams and FT score; read-only."""
import argparse, json, re, sqlite3, urllib.request, hashlib, unicodedata
from datetime import datetime, timezone
from pathlib import Path

BASE="https://api.openligadb.de/getmatchdata"
def norm(s):
    s=unicodedata.normalize("NFKD",(s or "").lower())
    s="".join(ch for ch in s if not unicodedata.combining(ch))
    s=re.sub(r"[^a-z0-9]+","",s)
    return s
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="football_model_database.sqlite")
    p.add_argument("--league",default="bl1")
    p.add_argument("--season",type=int,default=2026)
    p.add_argument("--competition-code",default="BUNDESLIGA")
    p.add_argument("--out",default="external_crosscheck_report.json")
    a=p.parse_args()
    url=f"{BASE}/{a.league}/{a.season}"
    req=urllib.request.Request(url,headers={"User-Agent":"football-db-crosscheck/1.0"})
    with urllib.request.urlopen(req,timeout=30) as r: raw=r.read()
    data=json.loads(raw.decode("utf-8"))
    con=sqlite3.connect(f"file:{a.db}?mode=ro",uri=True); con.row_factory=sqlite3.Row
    local=[dict(x) for x in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id JOIN competitions c ON c.competition_id=m.competition_id
      WHERE c.competition_code=?""",(a.competition_code,))]
    report={"timestamp_utc":datetime.now(timezone.utc).isoformat(),"provider":"OpenLigaDB","url":url,
      "payload_sha256":hashlib.sha256(raw).hexdigest(),"league_code":a.competition_code,
      "provider_records":len(data),"provider_finished_scored":0,"exact_matches":0,
      "score_conflicts":0,"unmatched_provider_records":0,"ambiguous_candidates":0,"examples":[],
      "limitations":["Community-maintained source; this is a cross-check, not official verification.",
      "Team names and kickoff time formats differ across sources; only exact normalized team/date/score matches count as exact.",
      "No database writes are performed."]}
    for row in data:
      if not isinstance(row,dict) or row.get("matchIsFinished") is not True: continue
      results=row.get("matchResults") or []
      ft=next((x for x in results if isinstance(x,dict) and str(x.get("resultTypeID","")) in {"2","3"} and isinstance(x.get("pointsTeam1"),int) and isinstance(x.get("pointsTeam2"),int)),None)
      if not ft: continue
      report["provider_finished_scored"]+=1
      home=norm((row.get("team1") or {}).get("teamName",""))
      away=norm((row.get("team2") or {}).get("teamName",""))
      dt=(row.get("matchDateTimeUTC") or row.get("matchDateTime") or "")[:10]
      candidates=[]
      for m in local:
        dbdate=(m["kickoff"] or "")[:10]
        if not dbdate or not dt: continue
        try:
          delta=abs((__import__("datetime").date.fromisoformat(dbdate)-__import__("datetime").date.fromisoformat(dt)).days)
        except ValueError:
          continue
        if delta > 1: continue
        if norm(m["home_team"])==home and norm(m["away_team"])==away:
          m=dict(m); m["date_delta_days"]=delta
          candidates.append(m)
      if len(candidates)>1:
        min_delta=min(x["date_delta_days"] for x in candidates)
        candidates=[x for x in candidates if x["date_delta_days"]==min_delta]
      if len(candidates)==1:
        m=candidates[0]
        if m["ft_home"]==ft["pointsTeam1"] and m["ft_away"]==ft["pointsTeam2"]:
          report["exact_matches"]+=1
        else:
          report["score_conflicts"]+=1
        if len(report["examples"])<30: report["examples"].append({"provider_id":row.get("matchID"),"date":dt,"provider_teams":[(row.get("team1") or {}).get("teamName"),(row.get("team2") or {}).get("teamName")],"db_match_id":m["match_id"],"db_teams":[m["home_team"],m["away_team"]],"provider_score":[ft["pointsTeam1"],ft["pointsTeam2"]],"db_score":[m["ft_home"],m["ft_away"]],"date_delta_days":m["date_delta_days"],"status":"exact_score_match" if (m["ft_home"],m["ft_away"])==(ft["pointsTeam1"],ft["pointsTeam2"]) else "score_conflict"})
      elif len(candidates)>1:
        report["ambiguous_candidates"]+=1
      else:
        report["unmatched_provider_records"]+=1
    Path(a.out).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    con.close()
if __name__=="__main__": main()
