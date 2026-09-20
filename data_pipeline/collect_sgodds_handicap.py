import io, re, sqlite3
from datetime import datetime
import pandas as pd
import requests

DB = "football_model_database.sqlite"
START, END = "2026-01-01", "2026-09-20"
PAGE = "https://sgodds.com/football/data"

LEAGUES = {
    "English Premier": ("EPL",),
    "French League": ("LIGUE1",),
    "German League": ("BUNDESLIGA",),
    "Italian League": ("SERIEA",),
    "Spanish League": ("LALIGA",),
    "J League": ("J1",),
    "K League": ("KLEAGUE1",),
}

ALIASES = {
    "manchesterunited": ["manunited", "manchesterunited"],
    "manchester city": ["mancity", "manchestercity"],
    "tottenhamhotspur": ["tottenham"],
    "newcastleunited": ["newcastle"],
    "wolverhamptonwanderers": ["wolves", "wolverhampton"],
    "westhamunited": ["westham"],
    "nottinghamforest": ["nottingham"],
    "brightonandhovealbion": ["brighton"],
    "parissaintgermain": ["psg"],
    "bayernmunich": ["bayernmunchen"],
    "borussiadortmund": ["dortmund"],
}

def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())

def similar(a, b):
    a, b = norm(a), norm(b)
    if a == b: return 1.0
    for k, vals in ALIASES.items():
        if a == k and b in [norm(v) for v in vals]: return 1.0
        if b == k and a in [norm(v) for v in vals]: return 1.0
    if a and b and (a in b or b in a): return 0.97
    return 0.0

def parse_date(x):
    d = pd.to_datetime(x, errors="coerce", dayfirst=False)
    if pd.isna(d):
        d = pd.to_datetime(x, errors="coerce", dayfirst=True)
    return None if pd.isna(d) else d.strftime("%Y-%m-%d")

def col(df, candidates):
    lower = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower: return lower[c.lower()]
    for c in df.columns:
        lc = str(c).strip().lower()
        if any(k in lc for k in candidates): return c
    return None

def fetch_index():
    r = requests.get(PAGE, timeout=30, headers={"User-Agent":"football-model-data-loader/1.0"})
    r.raise_for_status()
    links = re.findall(r'href=["\']([^"\']*?/downloads/[^"\']+\.csv)["\']', r.text, flags=re.I)
    out = {}
    for href in links:
        name = href.rsplit("/",1)[-1].lower()
        for league, codes in LEAGUES.items():
            key = league.lower().replace(" ","-")
            if key in name:
                out[league] = href if href.startswith("http") else "https://sgodds.com"+href
    # Current page uses stable league slugs; keep explicit fallback discovered in page HTML.
    for league, slug in [
        ("English Premier","english-premier"),("French League","french-league"),
        ("German League","german-league"),("Italian League","italian-league"),
        ("Spanish League","spanish-league"),("J League","j-league"),
        ("K League","k-league")]:
        if league not in out:
            m = re.search(r'href=["\']([^"\']*downloads/[^"\']*'+re.escape(slug)+r'\.csv)["\']', r.text, flags=re.I)
            if m:
                href=m.group(1); out[league]=href if href.startswith("http") else "https://sgodds.com"+href
    return out

def main():
    con = sqlite3.connect(DB)
    urls = fetch_index()
    totals = {"files":0,"rows":0,"integer_rows":0,"inserted":0,"unmatched":0}
    for league, code_tuple in LEAGUES.items():
        url = urls.get(league)
        if not url:
            print("WARN no CSV link:", league); continue
        try:
            raw = requests.get(url, timeout=45, headers={"User-Agent":"football-model-data-loader/1.0"}).content
            df = pd.read_csv(io.BytesIO(raw))
            date_c=col(df,["date","matchdate","game_date"])
            home_c=col(df,["home","hometeam","home_team"])
            away_c=col(df,["away","awayteam","away_team"])
            line_c=col(df,["ah_01_hcap","ah01hcap","ahhcap"])
            if not all([date_c,home_c,away_c,line_c]):
                print("WARN columns:",league,list(df.columns)[:20]); continue
            totals["files"] += 1
            for _, r in df.iterrows():
                d=parse_date(r.get(date_c))
                if not d or not (START <= d <= END): continue
                home=str(r.get(home_c,"")).strip(); away=str(r.get(away_c,"")).strip()
                if not home or not away or home=="nan" or away=="nan": continue
                try: line=float(r.get(line_c))
                except Exception: continue
                if abs(line-round(line)) > 1e-9:
                    continue
                totals["rows"] += 1
                cid=con.execute("SELECT competition_id FROM competitions WHERE competition_code=?",(code_tuple[0],)).fetchone()
                if not cid: continue
                matches=con.execute("""SELECT m.match_id,m.home_team,m.away_team
                    FROM matches m WHERE m.competition_id=? AND substr(m.kickoff,1,10)=?""",(cid[0],d)).fetchall()
                scored=[]
                for mid,dh,da in matches:
                    s=similar(home,dh)+similar(away,da)
                    if s>=1.9: scored.append((s,mid))
                if not scored:
                    totals["unmatched"] += 1; continue
                mid=max(scored,key=lambda x:x[0])[1]
                con.execute("""INSERT INTO sporttery_market
                    (match_id,pool_code,handicap,home_value,draw_value,away_value,captured_at,source_status)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (mid,"sgodds_open",line,None,None,None,datetime.utcnow().isoformat(timespec="seconds"),
                     "sgodds opening Asian handicap; integer lines only"))
                totals["inserted"] += 1
            con.commit()
            print("OK",league,"rows",len(df))
        except Exception as e:
            print("WARN",league,e)
    con.close()
    print("sgodds integer collector:",totals)

if __name__=="__main__": main()
