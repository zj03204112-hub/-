#!/usr/bin/env python3
"""Fetch real match-level xG/xGA from Understat and join to this DB conservatively.

Understat is a third-party, non-official source. The recorded observed_at is the
actual fetch timestamp, NOT an invented historical publication timestamp. Thus
these retroactively fetched records are useful for coverage research but must
fail the strict historical T-12h gate unless a genuine archived timestamp exists.
"""
import gzip, json, os, re, sqlite3, sys, time, unicodedata
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

DB = os.environ.get("FOOTBALL_DB", "football_model_database.sqlite")
OUT = os.environ.get("XG_INGEST_REPORT", "data/xg_ingest_report.json")
LEAGUES = ("EPL", "La_Liga", "Bundesliga", "Serie_A", "Ligue_1")
SEASONS = tuple(range(int(os.getenv("XG_SEASON_START", "2023")),
                      int(os.getenv("XG_SEASON_END", "2026")) + 1))
BASE = "https://understat.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; V5.2-xG-audit/1.0)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE,
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
def get_json(path):
    req = Request(BASE + path, headers=HEADERS)
    with urlopen(req, timeout=12) as response:
        raw = response.read()
        # Understat may return gzip-compressed bytes without urllib decoding them.
        if raw.startswith(bytes((31, 139))):
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8-sig"))

def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|afc|cf|sc|club|calcio|football|the)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)

def parse_dt(s):
    if not s: return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def getv(obj, *keys):
    for k in keys:
        if isinstance(obj, dict) and obj.get(k) is not None: return obj[k]
    return None

def extract_matches(payload, league, season):
    dates = payload.get("dates", []) if isinstance(payload, dict) else []
    out=[]
    for item in dates:
        if not isinstance(item, dict): continue
        h=item.get("h") or {}; a=item.get("a") or {}
        dt=parse_dt(getv(item,"datetime","date","kickoff"))
        xg=item.get("xG") or item.get("xg") or {}
        goals=item.get("goals") or {}
        hx=getv(xg,"h","home"); ax=getv(xg,"a","away")
        if not dt or not isinstance(h,dict) or not isinstance(a,dict): continue
        try: hx=float(hx); ax=float(ax)
        except (TypeError,ValueError): continue
        if not (0 <= hx <= 15 and 0 <= ax <= 15): continue
        if not h.get("title") or not a.get("title"): continue
        sid=str(getv(item,"id","match_id") or "")
        if not sid: continue
        out.append({
          "source_match_id":sid, "league":league, "season":season, "kickoff":dt,
          "home":str(h["title"]), "away":str(a["title"]), "home_xg":hx, "away_xg":ax,
          "source_url":f"https://understat.com/match/{sid}"
        })
    return out

def main():
    if not os.path.isfile(DB): raise SystemExit(f"DATABASE_NOT_FOUND:{DB}")
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    cols={r[1] for r in con.execute("PRAGMA table_info(matches)")}
    required={"match_id","kickoff","home_team","away_team"}
    if not required.issubset(cols): raise SystemExit(f"MATCH_SCHEMA_MISSING:{sorted(required-cols)}")
    dbrows=con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches").fetchall()
    indexed={}
    for r in dbrows:
        dt=parse_dt(r["kickoff"])
        if not dt: continue
        indexed.setdefault((norm(r["home_team"]),norm(r["away_team"])),[]).append((dt,str(r["match_id"]),str(r["home_team"]),str(r["away_team"])))
    report={"source":"Understat","source_homepage":BASE,"source_note":"Unofficial third-party xG model; retroactive fetch timestamp is not a historical publication timestamp.",
            "seasons":list(SEASONS),"leagues":list(LEAGUES),"fetched_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database_matches":len(dbrows),"source_matches_fetched":0,"matched_unique":0,"unmatched":0,"ambiguous":0,"rows_upserted":0,"errors":[]}
    fetched=[]
    for league in LEAGUES:
        for season in SEASONS:
            try:
                payload=get_json(f"getLeagueData/{league}/{season}")
                ms=extract_matches(payload,league,season)
                fetched.extend(ms)
                report["source_matches_fetched"]+=len(ms)
                print(json.dumps({"source":"Understat","league":league,"season":season,"matches":len(ms)}))
            except Exception as e:
                report["errors"].append({"league":league,"season":season,"error":str(e)[:300]})
            time.sleep(0.6)
    # Match by home/away names and nearest kickoff within 36 hours; never resolve ties by guess.
    matched=[]
    for s in fetched:
        candidates=indexed.get((norm(s["home"]),norm(s["away"])),[])
        near=[c for c in candidates if abs((c[0]-s["kickoff"]).total_seconds()) <= 36*3600]
        if len(near)==0:
            report["unmatched"]+=1; continue
        near.sort(key=lambda c:abs((c[0]-s["kickoff"]).total_seconds()))
        bestdiff=abs((near[0][0]-s["kickoff"]).total_seconds())
        best=[c for c in near if abs(abs((c[0]-s["kickoff"]).total_seconds())-bestdiff)<1]
        if len(best)!=1:
            report["ambiguous"]+=1; continue
        c=best[0]
        matched.append((s,c))
    # Create provenance-aware table. observed_at is the actual collection time.
    con.execute("""CREATE TABLE IF NOT EXISTS xg_observations (
      match_id TEXT NOT NULL, team_side TEXT NOT NULL CHECK(team_side IN ('home','away')),
      xg_for REAL NOT NULL CHECK(xg_for >= 0 AND xg_for <= 15),
      xg_against REAL NOT NULL CHECK(xg_against >= 0 AND xg_against <= 15),
      source_url TEXT NOT NULL, observed_at TEXT NOT NULL, source_status TEXT NOT NULL,
      PRIMARY KEY(match_id,team_side,source_url))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_xg_observations_match_side ON xg_observations(match_id,team_side)")
    # A single fetch timestamp per run is honest provenance. Do not backdate it to kickoff.
    observed=report["fetched_at"]
    for s,c in matched:
        mid=c[1]; url=s["source_url"]
        for side,xgf,xga in (("home",s["home_xg"],s["away_xg"]),("away",s["away_xg"],s["home_xg"])):
            con.execute("""INSERT INTO xg_observations
              (match_id,team_side,xg_for,xg_against,source_url,observed_at,source_status)
              VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(match_id,team_side,source_url) DO UPDATE SET
                xg_for=excluded.xg_for,xg_against=excluded.xg_against,
                observed_at=excluded.observed_at,source_status=excluded.source_status""",
              (mid,side,xgf,xga,url,observed,"verified"))
            report["rows_upserted"]+=1
    con.commit()
    report["matched_unique"]=len(matched)
    report["xg_observation_rows_total"]=con.execute("SELECT COUNT(*) FROM xg_observations").fetchone()[0]
    report["xg_observation_matches_total"]=con.execute("SELECT COUNT(DISTINCT match_id) FROM xg_observations").fetchone()[0]
    report["note"]="Historical xG is post-match information. Because this batch was fetched now, its observed_at must remain the real fetch time; it cannot pass a historical T-12h cutoff without genuine archived timestamps."
    os.makedirs(os.path.dirname(OUT) or ".",exist_ok=True)
    with open(OUT,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    con.close()
    if report["errors"] and not report["source_matches_fetched"]: raise SystemExit("UNDERSTAT_FETCH_FAILED")
if ( __name__ == "__main__"): main()
