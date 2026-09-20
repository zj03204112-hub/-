import hashlib, json, sqlite3, time, unicodedata, re
from difflib import SequenceMatcher
from datetime import datetime, timezone
import requests

DB="football_model_database.sqlite"
BASE="https://www.sofascore.com/api/v1"
START="2026-01-01"
END="2026-09-20"

# Public SofaScore event/lineup endpoints expose player minutes, starts, ratings,
# xG/xA and defensive actions. They are used as an enrichment source, not as a
# bookmaker input. See the README for the provenance caveat.
LEAGUES={
  "EPL":17, "LALIGA":8, "BUNDESLIGA":35, "SERIEA":23, "LIGUE1":34,
  "KLEAGUE1":410, "J1":196
}

def norm(s):
    s=unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]","",s)

TEAM_ALIASES={
  "psg":"parissaintgermain",
  "parissg":"parissaintgermain",
  "bayernmunich":"bayernmunchen",
  "intermilan":"inter",
  "sportinglisbon":"sportingcp"
}

def team_key(s):
    return TEAM_ALIASES.get(norm(s), norm(s))

def similarity(a,b):
    a,b=team_key(a),team_key(b)
    if not a or not b: return 0.0
    if a==b or a in b or b in a: return 1.0
    return SequenceMatcher(None,a,b).ratio()

def find_match(matches,home,away,d):
    target=datetime.fromisoformat(d).date()
    candidates=[]
    for (h,a,md),mid in matches.items():
        delta=abs((datetime.fromisoformat(md).date()-target).days)
        if delta>1: continue
        sh,sa=similarity(home,h),similarity(away,a)
        score=0.47*sh+0.47*sa+0.06*(1-delta)
        if sh>=0.72 and sa>=0.72:
            candidates.append((score,mid))
    if not candidates: return None
    candidates.sort(reverse=True)
    if len(candidates)>1 and candidates[0][0]-candidates[1][0]<0.025:
        return None
    return candidates[0][1]

def client():
    try:
        from curl_cffi import requests as cr
        return cr.Session(impersonate="chrome")
    except Exception:
        return requests.Session()

S=client()
S.headers.update({"User-Agent":"Mozilla/5.0 football-model-data-loader/2.0","Accept":"application/json"})

def get(path, tries=4):
    url=BASE+path
    for i in range(tries):
        try:
            r=S.get(url,timeout=30)
            if r.status_code==200:
                return r.json()
            if r.status_code in (403,429,567):
                time.sleep(2.5*(i+1))
            else:
                time.sleep(0.7*(i+1))
        except Exception:
            time.sleep(1.5*(i+1))
    return None

def val(st,*keys):
    for k in keys:
        x=st.get(k)
        if x is not None:
            try: return float(x)
            except Exception: pass
    return 0.0

def season_rows(tid):
    j=get(f"/unique-tournament/{tid}/seasons")
    return (j or {}).get("seasons",[])

def wanted_seasons(tid):
    out=[]
    for s in season_rows(tid):
        name=str(s.get("name",""))
        if ("25/26" in name or "2025/2026" in name or "2025/26" in name) and tid not in (410,196):
            out.append((s["id"],name))
        elif ("26/27" in name or "2026/2027" in name or "2026/27" in name):
            out.append((s["id"],name))
        elif tid in (410,196) and str(s.get("year",""))=="2026":
            out.append((s["id"],name))
    # newest first, unique ids
    seen=set(); return [(a,b) for a,b in out if not (a in seen or seen.add(a))]

def events(tid,sid):
    # Try both public SofaScore event-list routes. If one route silently
    # returns no data, the enrichment must not fail as a false green run.
    seen=set()
    for template in (
        "/unique-tournament/{tid}/season/{sid}/events/last/{page}",
        "/tournament/{tid}/season/{sid}/events/last/{page}",
    ):
        page=0
        got_any=False
        while page<40:
            j=get(template.format(tid=tid,sid=sid,page=page))
            if not j: break
            es=j.get("events",[])
            if not es: break
            got_any=True
            for e in es:
                eid=e.get("id")
                if eid not in seen:
                    seen.add(eid)
                    yield e
            if not j.get("hasNextPage"): break
            page+=1
        if got_any:
            return

def player_stats(e,side):
    out=[]
    block=e.get(side,{})
    for p in block.get("players",[]):
        pl=p.get("player") or {}
        st=p.get("statistics") or {}
        if not pl.get("id"): continue
        out.append({
          "player_id":str(pl["id"]),
          "player_name":pl.get("name") or pl.get("shortName") or "unknown",
          "position":p.get("position") or pl.get("position"),
          "starter":0 if p.get("substitute") else 1,
          "minutes":val(st,"minutesPlayed","minutes"),
          "rating":st.get("rating"),
          "goals":val(st,"goals"),
          "assists":val(st,"assists","goalAssist"),
          "xg":val(st,"expectedGoals","xg"),
          "xa":val(st,"expectedAssists","xa"),
          "shots":val(st,"totalShots","shots"),
          "key_passes":val(st,"keyPasses","keyPass"),
          "tackles":val(st,"tackles"),
          "interceptions":val(st,"interceptions"),
          "clearances":val(st,"clearances")
        })
    return out

def main():
    con=sqlite3.connect(DB)
    matches={}
    for r in con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches WHERE kickoff>=? AND kickoff<=?",(START,END+"T23:59:59")):
        matches[(norm(r[2]),norm(r[3]),r[1][:10])]=r[0]
    con.execute("DELETE FROM player_match_stats")
    mapped=0; rows=0; skipped=0; seen_events=0; lineup_failures=0
    for code,tid in LEAGUES.items():
        seasons=wanted_seasons(tid)
        print(json.dumps({"league":code,"seasons":seasons},ensure_ascii=False),flush=True)
        for sid,sname in seasons:
            season_events=0
            for e in events(tid,sid):
                season_events+=1; seen_events+=1
                ts=e.get("startTimestamp")
                if not ts: continue
                d=datetime.fromtimestamp(ts,tz=timezone.utc).date().isoformat()
                if not (START<=d<=END): continue
                home=((e.get("homeTeam") or {}).get("name",""))
                away=((e.get("awayTeam") or {}).get("name",""))
                mid=matches.get((norm(home),norm(away),d))
                if not mid:
                    mid=find_match(matches,home,away,d)
                if not mid:
                    skipped+=1; continue
                event_id=str(e.get("id"))
                con.execute("INSERT OR REPLACE INTO provider_event_map VALUES(?,?,?,?)",(mid,"sofascore",event_id,datetime.utcnow().isoformat(timespec="seconds")))
                lineup=get(f"/event/{event_id}/lineups")
                if not lineup:
                    lineup_failures+=1
                    continue
                for side in ("home","away"):
                    for p in player_stats(lineup,side):
                        con.execute("""INSERT OR REPLACE INTO player_match_stats
                        (match_id,team_side,player_id,player_name,position,starter,minutes_played,rating,goals,assists,xg,xa,shots,key_passes,tackles,interceptions,clearances,data_source,observed_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (mid,side,p["player_id"],p["player_name"],p["position"],p["starter"],p["minutes"],p["rating"],
                         p["goals"],p["assists"],p["xg"],p["xa"],p["shots"],p["key_passes"],p["tackles"],p["interceptions"],p["clearances"],
                         "SofaScore public lineup",datetime.utcnow().isoformat(timespec="seconds")))
                        rows+=1
                mapped+=1
                if mapped%50==0:
                    con.commit(); print(code,sname,"mapped",mapped,"player_rows",rows,flush=True)
                time.sleep(0.15)
            print(json.dumps({"league":code,"season":sname,"provider_events":season_events},ensure_ascii=False),flush=True)
    con.commit()
    print(json.dumps({"mapped_matches":mapped,"player_rows":rows,"unmatched_events":skipped,"seen_provider_events":seen_events,"lineup_failures":lineup_failures},ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
