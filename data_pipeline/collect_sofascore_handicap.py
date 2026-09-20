import json, re, sqlite3, time, unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone
import requests

DB="football_model_database.sqlite"
BASES=["https://api.sofascore.com/api/v1","https://www.sofascore.com/api/v1"]
START="2026-01-01"; END="2026-09-20"
LEAGUES={17,8,35,23,34,410,196}
ALIASES={"psg":"parissaintgermain","parissg":"parissaintgermain","bayernmunich":"bayernmunchen","intermilan":"inter","sportinglisbon":"sportingcp"}
PROVIDER_TIMEOUT=8
MAX_RETRIES=1

def norm(s):
    s=unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]","",s)
def sim(a,b):
    a=ALIASES.get(norm(a),norm(a)); b=ALIASES.get(norm(b),norm(b))
    if not a or not b:return 0.0
    if a==b or a in b or b in a:return 1.0
    return SequenceMatcher(None,a,b).ratio()

try:
    from curl_cffi import requests as cr
    S=cr.Session(impersonate="chrome")
except Exception:
    S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 Chrome/140 Safari/537.36","Accept":"application/json,text/plain,*/*",
                  "Referer":"https://www.sofascore.com/","Origin":"https://www.sofascore.com",
                  "x-requested-with":"XMLHttpRequest"})

def get(path):
    for base in BASES:
        for i in range(MAX_RETRIES + 1):
            try:
                r=S.get(base+path,timeout=PROVIDER_TIMEOUT)
                if r.status_code==200:
                    return r.json() or {}
                if r.status_code in (403,429,567):
                    break
            except Exception:
                pass
            if i < MAX_RETRIES:
                time.sleep(1)
    return {}

def frac_to_decimal(x):
    try:
        if isinstance(x,(int,float)): return float(x)
        s=str(x or "").strip()
        if "/" in s:
            a,b=s.split("/",1); return float(a)/float(b)+1.0
        return float(s)+1.0
    except Exception:return None

def parse_choice(name):
    s=str(name or "")
    m=re.search(r"\(([-+]?\d+(?:\.\d+)?)\)",s)
    if not m:return None,None
    line=float(m.group(1))
    team=re.sub(r"^\s*\([-+]?\d+(?:\.\d+)?\)\s*","",s).strip()
    return line,team

def target_event(e):
    ut=e.get("uniqueTournament") or {}
    return ut.get("id") is not None and int(ut["id"]) in LEAGUES

def main():
    con=sqlite3.connect(DB)
    matches={}
    dates=set()
    for mid,ko,h,a in con.execute("""
        SELECT m.match_id,m.kickoff,m.home_team,m.away_team
        FROM matches m
        WHERE m.kickoff>=? AND m.kickoff<=?
    """,(START,END+"T23:59:59")):
        d=ko[:10]; dates.add(d)
        has_ah=con.execute("""
            SELECT 1 FROM sporttery_market
            WHERE match_id=? AND pool_code IN ('asian_handicap_avg','sofascore_asian_featured')
            LIMIT 1
        """,(mid,)).fetchone() is not None
        matches[(norm(h),norm(a),d)]=(mid,h,a,has_ah)

    mapped=rows=events=zero_odds=skipped_existing=0
    for idx,d in enumerate(sorted(dates),1):
        payload=get(f"/sport/football/scheduled-events/{d}")
        for e in payload.get("events") or []:
            if not target_event(e):continue
            ed=datetime.fromtimestamp(e.get("startTimestamp",0),tz=timezone.utc).date().isoformat() if e.get("startTimestamp") else d
            if not START<=ed<=END:continue
            home=(e.get("homeTeam") or {}).get("name",""); away=(e.get("awayTeam") or {}).get("name","")
            rec=matches.get((norm(home),norm(away),ed))
            if not rec:
                cand=[]
                for (hh,aa,md),v in matches.items():
                    if abs((datetime.fromisoformat(md).date()-datetime.fromisoformat(ed).date()).days)<=1 and sim(home,hh)>=.72 and sim(away,aa)>=.72:
                        cand.append((sim(home,hh)+sim(away,aa),v))
                if cand: rec=max(cand,key=lambda x:x[0])[1]
            if not rec:continue
            mid,dbhome,dbaway,has_ah=rec; events+=1
            if has_ah:
                skipped_existing+=1
                continue
            odds=get(f"/event/{e.get('id')}/odds/1/featured")
            asian=(odds.get("featured") or {}).get("asian") or {}
            choices=asian.get("choices") or []
            parsed=[]
            for ch in choices:
                line,team=parse_choice(ch.get("name"))
                dec=frac_to_decimal(ch.get("fractionalValue"))
                if line is not None and dec and dec>1 and team:
                    side="home" if sim(team,dbhome)>=sim(team,dbaway) else "away"
                    parsed.append((line,side,dec,team))
            pair=None
            for h in parsed:
                for a in parsed:
                    if h is a or h[1]!="home" or a[1]!="away":continue
                    if abs(h[0]+a[0])<0.01:
                        pair=(h,a);break
                if pair:break
            if not pair:
                zero_odds+=1; continue
            h,a=pair
            con.execute("DELETE FROM sporttery_market WHERE match_id=? AND pool_code='sofascore_asian_featured'",(mid,))
            con.execute("""INSERT INTO sporttery_market
              (match_id,pool_code,handicap,home_value,draw_value,away_value,captured_at,source_status)
              VALUES (?,?,?,?,?,?,?,?)""",(mid,"sofascore_asian_featured",h[0],h[2],None,a[2],
              datetime.now(timezone.utc).isoformat(timespec="seconds"),"SofaScore featured Asian handicap"))
            rows+=1; mapped+=1
        if idx%10==0:con.commit()
        print(json.dumps({"date":d,"progress":f"{idx}/{len(dates)}","mapped":mapped,"rows":rows,
                          "events":events,"no_asian":zero_odds,"skipped_existing":skipped_existing}),flush=True)
    con.commit(); con.close()
    print(json.dumps({"mapped_matches":mapped,"market_rows":rows,"target_events":events,
                      "no_asian":zero_odds,"skipped_existing":skipped_existing},ensure_ascii=False))
if __name__=="__main__":main()
