#!/usr/bin/env python3
# Sporttery 2026 seven-league collector
# Scope: EPL, La Liga, Bundesliga, Serie A, Ligue 1, K League 1, J1 League.
# League matches only; excludes cups, Europe, friendlies and national teams.
import csv, requests
from datetime import date, timedelta

API="https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
LEAGUES={"英超","西甲","德甲","意甲","法甲","韩K联","日职","J1","K1"}
HEADERS={"User-Agent":"Mozilla/5.0","Referer":"https://www.sporttery.cn/","Accept":"application/json, text/plain, */*"}

def fetch(d1,d2):
    r=requests.get(API,params={"startDate":d1,"endDate":d2},headers=HEADERS,timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    out="model_validation/sporttery_target_leagues_2026.csv"
    seen=set(); rows=[]
    d=date(2026,1,1); end=date(2026,9,30)
    while d<=end:
        e=min(d+timedelta(days=30),end)
        data=fetch(d.isoformat(),e.isoformat())
        matches=(data.get("value") or {}).get("matchResult",[])
        for m in matches:
            league=str(m.get("leagueName") or m.get("leagueShortName") or "")
            if not any(x in league for x in LEAGUES): continue
            subs=m.get("subMatchList") or [m]
            for x in subs:
                home=x.get("homeTeamName") or x.get("homeTeam") or ""
                away=x.get("awayTeamName") or x.get("awayTeam") or ""
                hs=x.get("homeScore"); a=x.get("awayScore")
                if not home or not away or hs in (None,"") or a in (None,""): continue
                key=(str(x.get("matchId") or m.get("matchId") or ""),home,away,str(x.get("matchDate") or m.get("matchDate") or ""))
                if key in seen: continue
                seen.add(key)
                rows.append([x.get("matchDate") or m.get("matchDate"),league,home,away,hs,a,x.get("goalLine") or m.get("goalLine"),x.get("matchId") or m.get("matchId")])
        d=e+timedelta(days=1)
    with open(out,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f); w.writerow(["date","league","home","away","home_score","away_score","handicap","match_id"]); w.writerows(rows)
    print(len(rows))

if __name__=="__main__": main()
