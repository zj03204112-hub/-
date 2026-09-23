import os, sqlite3, time, re, unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone
import requests

DB="football_model_database.sqlite"; API_KEY=os.getenv("API_FOOTBALL_KEY")
BASE="https://v3.football.api-sports.io"; START="2026-01-01"; END="2026-09-20"
LEAGUES={39:("Premier League",(2025,2026)),140:("LaLiga",(2025,2026)),78:("Bundesliga",(2025,2026)),135:("Serie A",(2025,2026)),61:("Ligue 1",(2025,2026)),98:("J1 League",(2026,)),292:("K League 1",(2026,))}
ALIASES={"psg":"parissaintgermain","parissaintgermain":"parissaintgermain","bayernmunich":"bayernmunchen","bayernmunchen":"bayernmunchen","intermilan":"inter","internazionale":"inter","sportinglisbon":"sportingcp","sportingcp":"sportingcp","manutd":"manchesterunited","manchesterutd":"manchesterunited","manchesterunitedfc":"manchesterunited","mancity":"mancity","manchestercity":"mancity","tottenhamhotspur":"tottenham","tottenham":"tottenham","athleticbilbao":"athleticclub","athleticclub":"athleticclub","borussiadortmund":"dortmund","dortmund":"dortmund","borussiamonchengladbach":"monchengladbach","monchengladbach":"monchengladbach"}
def norm(s):
    s=unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower(); return re.sub(r"[^a-z0-9]","",s)
def team_key(s):
    n=norm(s)
    for suffix in ("footballclub","fc","cf","calcio","1899"):
        if n.endswith(suffix) and len(n)>len(suffix)+4:n=n[:-len(suffix)]
    return ALIASES.get(n,n)
def similarity(a,b):
    a,b=team_key(a),team_key(b)
    if not a or not b:return 0
    if a==b or a in b or b in a:return 1
    ta=set(re.findall(r"[a-z0-9]+",a)); tb=set(re.findall(r"[a-z0-9]+",b))
    j=len(ta&tb)/max(1,len(ta|tb))
    return max(SequenceMatcher(None,a,b).ratio(),j)
S=requests.Session(); S.headers.update({"x-apisports-key":API_KEY,"User-Agent":"football-model-player-collector/1.1"})
def get(endpoint,params,tries=3):
    for i in range(tries):
        try:
            r=S.get(f"{BASE}/{endpoint}",params=params,timeout=30)
            if r.status_code in (429,500,502,503,504):time.sleep(2+i*2);continue
            r.raise_for_status(); data=r.json()
            if data.get("errors"):raise RuntimeError(str(data["errors"]))
            return data
        except Exception:
            if i==tries-1:raise
            time.sleep(1+i)
    raise RuntimeError("unreachable")
def fixture_rows(lid,season):
    return get("fixtures",{"league":lid,"season":season,"from":START,"to":END}).get("response",[])
def map_fixture(matches,item):
    dt=item["fixture"].get("date","")[:10]; teams=item["teams"]; c=[]
    for (h,a,d),mid in matches.items():
        dd=abs((datetime.fromisoformat(d).date()-datetime.fromisoformat(dt).date()).days)
        if dd>1:continue
        sh,sa=similarity(teams["home"]["name"],h),similarity(teams["away"]["name"],a)
        if sh<.65 or sa<.65:continue
        c.append((.45*sh+.45*sa+.10*(dd==0),sh,sa,dd,mid))
    if not c:return None
    c.sort(reverse=True)
    if len(c)>1 and c[0][0]-c[1][0]<.01:return None
    if c[0][1] < .55 or c[0][2] < .55:return None
    return c[0][-1]
def parse_players(item,mid):
    fixture_id=item["fixture"]["id"]
    data=get("fixtures",{"ids":fixture_id})
    detail=(data.get("response") or [])
    detail=detail[0] if detail else {}
    blocks=detail.get("players") or []
    if not blocks:
        data=get("fixtures/players",{"fixture":fixture_id})
        blocks=data.get("response") or []
    out=[]
    for block in blocks:
        side="home" if block.get("team",{}).get("id")==item["teams"]["home"].get("id") else "away"
        for p in block.get("players",[]):
            player=p.get("player") or {}; st=(p.get("statistics") or [{}])[0]
            if not player.get("id") or not player.get("name"):continue
            g=st.get("games") or {}; shots=st.get("shots") or {}; goals=st.get("goals") or {}; passes=st.get("passes") or {}; tackles=st.get("tackles") or {}
            out.append((mid,side,str(player["id"]),player["name"],g.get("position"),0 if g.get("substitute") else 1,float(g.get("minutes") or 0),float(g.get("rating") or 0) if g.get("rating") else None,float(goals.get("total") or 0),float(goals.get("assists") or 0),0.0,0.0,float(shots.get("total") or 0),float(passes.get("key") or 0),float(tackles.get("total") or 0),float(tackles.get("interceptions") or 0),float(tackles.get("blocks") or 0),"API-Football",datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return out
def main():
    if not API_KEY:raise SystemExit("API_FOOTBALL_KEY is not configured")
    con=sqlite3.connect(DB); matches={}
    for mid,kickoff,home,away in con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches WHERE kickoff>=? AND kickoff<=?",(START,END+"T23:59:59")):
        matches[(team_key(home),team_key(away),kickoff[:10])]=mid
    total=mapped=rows=failures=0; seen=set(); league_stats={}
    for lid,(name,seasons) in LEAGUES.items():
        ls={"fixtures":0,"mapped":0,"rows":0,"errors":0}
        for season in seasons:
            try:items=fixture_rows(lid,season)
            except Exception as e:ls["errors"]+=1;failures+=1;print(f"{name} season {season}: ERROR {e}",flush=True);continue
            for item in items:
                total+=1;ls["fixtures"]+=1;mid=map_fixture(matches,item)
                if not mid or mid in seen:continue
                if (item.get("fixture",{}).get("status") or {}).get("short") not in {"FT","AET","PEN"}:continue
                try:parsed=parse_players(item,mid)
                except Exception as e:failures+=1;ls["errors"]+=1;print(f"{name} fixture {item['fixture']['id']} player error: {e}",flush=True);continue
                if not parsed:continue
                seen.add(mid);mapped+=1;ls["mapped"]+=1
                con.executemany("""INSERT OR REPLACE INTO player_match_stats
                (match_id,team_side,player_id,player_name,position,starter,minutes_played,rating,goals,assists,xg,xa,shots,key_passes,tackles,interceptions,clearances,data_source,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",parsed)
                rows+=len(parsed);ls["rows"]+=len(parsed)
                if mapped%25==0:con.commit()
        league_stats[name]=ls;print(f"{name}: {ls}",flush=True)
    con.commit(); source_rows=con.execute("SELECT data_source,COUNT(*) FROM player_match_stats GROUP BY data_source").fetchall(); db_matches=con.execute("SELECT COUNT(DISTINCT match_id) FROM player_match_stats").fetchone()[0]
    print({"provider":"API-Football","fixtures_seen":total,"mapped_matches":mapped,"player_rows_written":rows,"db_distinct_matches":db_matches,"source_rows":source_rows,"league_stats":league_stats,"failures":failures},flush=True);con.close()
    if rows==0:raise SystemExit("API-Football player collector returned zero player rows")
if __name__=="__main__":main()
