import os,sqlite3,requests,hashlib
from datetime import datetime
DB="football_model_database.sqlite"; KEY=os.getenv("API_FOOTBALL_KEY"); BASE="https://v3.football.api-sports.io"
LEAGUE_ID=5; SEASON=2026; START="2026-01-01"; END="2026-09-20"
def api(path,params):
    r=requests.get(f"{BASE}/{path}",params=params,headers={"x-apisports-key":KEY,"User-Agent":"football-model-nations-league/1.0"},timeout=30)
    r.raise_for_status(); d=r.json()
    if d.get("errors"): raise RuntimeError(str(d["errors"]))
    return d.get("response",[])
def mid(date,home,away): return hashlib.sha1(f"NATIONS_LEAGUE|{date}|{home}|{away}".encode()).hexdigest()[:20]
def main():
    if not KEY: raise SystemExit("API_FOOTBALL_KEY is not configured")
    con=sqlite3.connect(DB)
    con.execute("INSERT OR IGNORE INTO competitions(competition_id,competition_code,competition_name,country) VALUES(8,'NATIONS_LEAGUE','UEFA Nations League','Europe')")
    con.execute("INSERT OR IGNORE INTO seasons(season_id,competition_id,season_label,start_date,end_date) VALUES(13,8,'2026/27','2026-09-01','2027-06-30')")
    items=api("fixtures",{"league":LEAGUE_ID,"season":SEASON,"from":START,"to":END})
    added=finished=0
    for x in items:
        f=x.get("fixture",{}); t=x.get("teams",{}); date=f.get("date","")[:10]
        if not date or not (START<=date<=END): continue
        home=t.get("home",{}).get("name"); away=t.get("away",{}).get("name")
        if not home or not away: continue
        status=(f.get("status") or {}).get("short"); m=mid(date,home,away)
        con.execute("""INSERT OR IGNORE INTO matches(match_id,competition_id,season_id,kickoff,home_team,away_team,status,source_status,primary_source_id)
                       VALUES(?,?,?,?,?,?,?,?,?)""",(m,8,13,f.get("date"),home,away,"finished" if status in {"FT","AET","PEN"} else "scheduled","api_football",None))
        added+=1
        if status in {"FT","AET","PEN"}:
            s=x.get("score",{}); ft=s.get("fulltime") or {}; ht=s.get("halftime") or {}
            if ft.get("home") is not None and ft.get("away") is not None:
                con.execute("""INSERT OR REPLACE INTO results(match_id,ht_home,ht_away,ft_home,ft_away,result_1x2,completed_at,source_status)
                               VALUES(?,?,?,?,?,?,?,?)""",(m,ht.get("home"),ht.get("away"),ft.get("home"),ft.get("away"),
                               "H" if ft.get("home")>ft.get("away") else "A" if ft.get("home")<ft.get("away") else "D",date,"api_football"))
                finished+=1
    con.commit()
    print({"competition":"UEFA Nations League","season":SEASON,"fixtures_seen":len(items),"matches_added":added,"finished_results":finished},flush=True)
    con.close()
if __name__=="__main__": main()
