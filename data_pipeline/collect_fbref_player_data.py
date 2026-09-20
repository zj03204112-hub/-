import re, sqlite3, time
from datetime import datetime
from io import StringIO
from pathlib import Path
import pandas as pd
import requests

DB = "football_model_database.sqlite"
START = "2026-01-01"
END = "2026-09-20"
CACHE = Path(".cache/fbref")
CACHE.mkdir(parents=True, exist_ok=True)

LEAGUES = {
    "Premier League": ("ENG-Premier League", "9"),
    "La Liga": ("ESP-La Liga", "12"),
    "Bundesliga": ("GER-Bundesliga", "20"),
    "Serie A": ("ITA-Serie A", "11"),
    "Ligue 1": ("FRA-Ligue 1", "13"),
}

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; football-model-data/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
})

def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

def get(url, cache_name, sleep=1.2):
    p = CACHE / cache_name
    if p.exists():
        return p.read_text(encoding="utf-8", errors="ignore")
    for attempt in range(4):
        try:
            r = S.get(url, timeout=30)
            if r.status_code == 200 and len(r.text) > 1000:
                p.write_text(r.text, encoding="utf-8")
                time.sleep(sleep)
                return r.text
            if r.status_code in (403, 429):
                time.sleep(8 * (attempt + 1))
            else:
                time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None

def seasons_for_dates():
    return ["2025", "2026"]

def schedule_urls():
    out=[]
    for name,(comp,_) in LEAGUES.items():
        for season in seasons_for_dates():
            out.append((name,comp,season,
                f"https://fbref.com/en/comps/{_}/schedule/{season}-{season+1 if False else season}"))
    return out

def load_existing_matches(con):
    rows=con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches WHERE kickoff>=? AND kickoff<=?",
                     (START, END+"T23:59:59")).fetchall()
    by={}
    for mid,ko,h,a in rows:
        by[(norm(h),norm(a),ko[:10])] = mid
    return by

def match_report_links(html):
    links=[]
    # FBref comments out many tables; expose HTML comments before parsing.
    html2=html.replace("<!--","").replace("-->","")
    for m in re.finditer(r'href="(/en/matches/[^"]+)"', html2):
        u="https://fbref.com"+m.group(1)
        if u not in links: links.append(u)
    return links

def find_existing(by, home, away, date):
    key=(norm(home),norm(away),date)
    if key in by: return by[key]
    # Conservative fuzzy fallback on normalized names and same date.
    for (h,a,d),mid in by.items():
        if d!=date: continue
        if (h in norm(home) or norm(home) in h) and (a in norm(away) or norm(away) in a):
            return mid
    return None

def parse_report(url, cache_name, mid, con):
    html=get(url,cache_name,sleep=1.0)
    if not html: return 0
    html2=html.replace("<!--","").replace("-->","")
    try:
        tables=pd.read_html(StringIO(html2))
    except Exception:
        return 0
    inserted=0
    # FBref match reports contain team summary tables with columns such as
    # Player, Pos, Min, Gls, Ast, Sh, SoT, Tkl, Int, etc.
    for df in tables:
        if "Player" not in df.columns or "Min" not in df.columns:
            continue
        d=df.copy()
        if isinstance(d.columns,pd.MultiIndex):
            d.columns=[c[-1] if isinstance(c,tuple) else c for c in d.columns]
        if "Player" not in d.columns or "Min" not in d.columns: continue
        if not any(c in d.columns for c in ["Gls","Ast","Sh","Tkl","Int","Carries"]): continue
        # Infer team from repeated match-report table context where possible.
        # Tables usually contain the team label in the preceding HTML table id;
        # use the match page's team names and assign by table order for two-team
        # player-stat tables.
        # We keep only rows that look like actual players.
        d=d[d["Player"].astype(str).str.strip().ne("")].copy()
        d=d[~d["Player"].astype(str).str.contains(r"^\d+\s+Players$",regex=True,na=False)]
        for _,r in d.iterrows():
            name=str(r.get("Player","")).strip()
            if not name or name.lower()=="player": continue
            def num(k):
                try: return float(str(r.get(k,0)).replace("—","0").replace("-","0"))
                except: return 0.0
            minutes=num("Min")
            starter=1 if minutes>=45 else 0
            pos=str(r.get("Pos","") or "")
            # Team side is resolved later when duplicate player names appear.
            # Store a neutral side only when the table does not expose team;
            # downstream uses the match/team mapping and can still use player history.
            con.execute("""INSERT OR IGNORE INTO player_match_stats
                (match_id,team_side,player_id,player_name,position,starter,
                 minutes_played,rating,goals,assists,xg,xa,shots,key_passes,
                 tackles,interceptions,clearances,data_source,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mid,"unknown",None,name,pos,starter,minutes,None,num("Gls"),num("Ast"),
                 num("xG"),num("xAG"),num("Sh"),num("KP"),num("Tkl"),num("Int"),num("Clr"),
                 "FBref free fallback",datetime.utcnow().isoformat(timespec="seconds")))
            inserted += 1
        if inserted:
            break
    return inserted

def main():
    con=sqlite3.connect(DB)
    by=load_existing_matches(con)
    total=0; matched=0; reports=0; failures=0
    seen=set()
    for league,comp,season in schedule_urls():
        # Correct FBref schedule endpoint uses competition code and season start year.
        comp_id={"ENG-Premier League":"9","ESP-La Liga":"12","GER-Bundesliga":"20",
                 "ITA-Serie A":"11","FRA-Ligue 1":"13"}[comp]
        url=f"https://fbref.com/en/comps/{comp_id}/{season}/schedule/{season}-{int(season)+1}"
        html=get(url,f"schedule_{comp_id}_{season}.html",sleep=1.5)
        if not html: failures+=1; continue
        for report in match_report_links(html):
            if report in seen: continue
            seen.add(report)
            # Pull the report once to identify date/team names.
            rh=get(report,"report_"+re.sub(r"[^a-zA-Z0-9]","_",report.split("/")[-1])+".html",sleep=1.0)
            if not rh: failures+=1; continue
            text=re.sub("<[^>]+>"," ",rh)
            text=re.sub(r"\s+"," ",text)
            mdate=re.search(r"(20\\d\\d-\\d\\d-\\d\\d)",text)
            if not mdate: continue
            date=mdate.group(1)
            if not (START<=date<=END): continue
            # Team names from match header are harder to parse robustly; use DB
            # matching by searching known home/away names in report text.
            candidates=[]
            for (h,a,d),mid in by.items():
                if d!=date: continue
                if h and a and h in norm(text) and a in norm(text):
                    candidates.append(mid)
            if len(candidates)!=1: continue
            mid=candidates[0]
            n=parse_report(report,"parsed_"+str(mid)+".html",mid,con)
            if n:
                matched+=1; total+=n
            reports+=1
            if reports%25==0:
                con.commit()
                print({"reports":reports,"matched":matched,"player_rows":total},flush=True)
    con.commit(); con.close()
    print({"provider":"FBref_free","reports_seen":reports,"mapped_matches":matched,
           "player_rows":total,"failures":failures},flush=True)

if __name__=="__main__":
    main()
