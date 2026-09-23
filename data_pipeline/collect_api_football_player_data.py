import os, sqlite3, time, re, unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone

import requests

DB = "football_model_database.sqlite"
API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE = "https://v3.football.api-sports.io"
START = "2026-01-01"
END = "2026-09-20"

LEAGUES = {
    39: "Premier League",
    140: "LaLiga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    98: "J1 League",
    292: "K League 1",
}

ALIASES = {
    "psg":"parissaintgermain","parissaintgermain":"parissaintgermain",
    "bayernmunich":"bayernmunchen","bayernmunchen":"bayernmunchen",
    "intermilan":"inter","internazionale":"inter",
    "sportinglisbon":"sportingcp","sportingcp":"sportingcp",
    "manutd":"manchesterunited","manchesterutd":"manchesterunited",
    "manchesterunitedfc":"manchesterunited",
    "mancity":"mancity","manchestercity":"mancity",
    "tottenhamhotspur":"tottenham","tottenham":"tottenham",
    "athleticbilbao":"athleticclub","athleticclub":"athleticclub",
    "borussiadortmund":"dortmund","dortmund":"dortmund",
    "borussiamonchengladbach":"monchengladbach","monchengladbach":"monchengladbach",
}

def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]","",s)

def team_key(s):
    n = norm(s)
    for suffix in ("footballclub","fc","cf","calcio","1899"):
        if n.endswith(suffix) and len(n) > len(suffix)+4:
            n = n[:-len(suffix)]
    return ALIASES.get(n,n)

def similarity(a,b):
    a,b=team_key(a),team_key(b)
    if not a or not b: return 0.0
    if a==b or a in b or b in a: return 1.0
    return SequenceMatcher(None,a,b).ratio()

S=requests.Session()
S.headers.update({"x-apisports-key": API_KEY, "User-Agent":"football-model-player-collector/1.0"})

def get(endpoint, params, tries=3):
    for i in range(tries):
        try:
            r=S.get(f"{BASE}/{endpoint}", params=params, timeout=30)
            if r.status_code in (429,500,502,503,504):
                time.sleep(2+i*2); continue
            r.raise_for_status()
            data=r.json()
            if data.get("errors"):
                raise RuntimeError(str(data["errors"]))
            return data
        except Exception:
            if i == tries-1: raise
            time.sleep(1+i)
    raise RuntimeError("unreachable")

def fixture_rows(league_id):
    data=get("fixtures",{"league":league_id,"season":2026,"from":START,"to":END})
    return data.get("response",[])

def map_fixture(matches, item):
    fixture=item["fixture"]; teams=item["teams"]
    dt=fixture.get("date","")[:10]
    home=teams["home"]["name"]; away=teams["away"]["name"]
    candidates=[]
    for (h,a,d),mid in matches.items():
        dd=abs((datetime.fromisoformat(d).date()-datetime.fromisoformat(dt).date()).days)
        if dd>1: continue
        sh,sa=similarity(home,h),similarity(away,a)
        if sh<0.70 or sa<0.70: continue
        score=0.45*sh+0.45*sa+0.10*(dd==0)
        candidates.append((score,sh,sa,dd,mid))
    if not candidates: return None
    candidates.sort(reverse=True)
    if len(candidates)>1 and candidates[0][0]-candidates[1][0]<0.03: return None
    return candidates[0][-1]

def parse_players(item, mid):
    fixture_id=item["fixture"]["id"]
    data=get("fixtures/players",{"fixture":fixture_id})
    out=[]
    for team_block in data.get("response",[]):
        team=team_block.get("team") or {}
        side = "home" if team.get("id")==item["teams"]["home"].get("id") else "away"
        for p in team_block.get("players",[]):
            player=p.get("player") or {}
            st=p.get("statistics") or []
            st=st[0] if st else {}
            if not player.get("id") or not player.get("name"): continue
            shots=st.get("shots") or {}
            goals=st.get("goals") or {}
            passes=st.get("passes") or {}
            tackles=st.get("tackles") or {}
            duels=st.get("duels") or {}
            dribbles=st.get("dribbles") or {}
            cards=st.get("cards") or {}
            out.append((
                mid,side,str(player["id"]),player["name"],st.get("games",{}).get("position"),
                0 if st.get("games",{}).get("substitute") else 1,
                float(st.get("games",{}).get("minutes") or 0),
                float(st.get("games",{}).get("rating") or 0) if st.get("games",{}).get("rating") else None,
                float(goals.get("total") or 0),float(goals.get("assists") or 0),
                0.0,0.0,float(shots.get("total") or 0),float(passes.get("key") or 0),
                float(tackles.get("total") or 0),float(tackles.get("interceptions") or 0),
                float(tackles.get("blocks") or 0), "API-Football",
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            ))
    return out

def main():
    if not API_KEY:
        raise SystemExit("API_FOOTBALL_KEY is not configured")

    con=sqlite3.connect(DB)
    matches={}
    for mid,kickoff,home,away in con.execute(
        "SELECT match_id,kickoff,home_team,away_team FROM matches WHERE kickoff>=? AND kickoff<=?",
        (START,END+"T23:59:59")):
        matches[(team_key(home),team_key(away),kickoff[:10])]=mid

    total_fixtures=0; mapped=0; rows=0; failures=0; leagues_ok=0
    seen_mids=set()
    for lid,name in LEAGUES.items():
        try:
            items=fixture_rows(lid); leagues_ok+=1
            print(f"{name}: fixtures={len(items)}",flush=True)
        except Exception as e:
            print(f"{name}: ERROR {e}",flush=True); failures+=1; continue
        for item in items:
            total_fixtures+=1
            mid=map_fixture(matches,item)
            if not mid or mid in seen_mids: continue
            status=(item.get("fixture",{}).get("status") or {}).get("short")
            if status not in {"FT","AET","PEN"}: continue
            try:
                parsed=parse_players(item,mid)
            except Exception as e:
                failures+=1
                print(f"fixture {item['fixture']['id']} player error: {e}",flush=True)
                continue
            if not parsed: continue
            seen_mids.add(mid); mapped+=1
            con.executemany(
                """INSERT OR REPLACE INTO player_match_stats
                (match_id,team_side,player_id,player_name,position,starter,
                 minutes_played,rating,goals,assists,xg,xa,shots,key_passes,
                 tackles,interceptions,clearances,data_source,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",parsed)
            rows+=len(parsed)
            if mapped%25==0: con.commit()
            time.sleep(0.05)
    con.commit()
    print({
        "provider":"API-Football","fixtures_seen":total_fixtures,
        "mapped_matches":mapped,"player_rows":rows,
        "leagues_ok":leagues_ok,"failures":failures
    },flush=True)
    con.close()
    if rows==0:
        raise SystemExit("API-Football player collector returned zero player rows")

if __name__=="__main__":
    main()
