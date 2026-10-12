#!/usr/bin/env python3
"""Fetch real match-level xG/xGA from Understat and join to this DB conservatively.

Understat is a third-party, non-official source. The recorded observed_at is the
actual fetch timestamp, NOT an invented historical publication timestamp. Thus
these retroactively fetched records are useful for coverage research but must
fail the strict historical T-12h gate unless a genuine archived timestamp exists.
"""
import csv, difflib, gzip, json, os, re, sqlite3, sys, time, unicodedata
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

DB = os.environ.get("FOOTBALL_DB", "football_model_database.sqlite")
OUT = os.environ.get("XG_INGEST_REPORT", "data/xg_ingest_report.json")
UNMATCHED_OUT = os.environ.get("XG_UNMATCHED_CSV", "data/xg_unmatched_diagnosis.csv")
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
    # Preserve every unmatched source row and provide a conservative candidate diagnosis.
    matched=[]
    unmatched_rows=[]
    all_db=[]
    for r in dbrows:
        dt=parse_dt(r["kickoff"])
        if dt:
            all_db.append((dt,str(r["match_id"]),str(r["home_team"]),str(r["away_team"])))
    for s in fetched:
        candidates=indexed.get((norm(s["home"]),norm(s["away"])),[])
        near=[c for c in candidates if abs((c[0]-s["kickoff"]).total_seconds()) <= 36*3600]
        if len(near)==0:
            report["unmatched"]+=1
            # First diagnose same normalized pair with a kickoff mismatch.
            exact_pair=indexed.get((norm(s["home"]),norm(s["away"])),[])
            if exact_pair:
                ranked=sorted(exact_pair,key=lambda c:abs((c[0]-s["kickoff"]).total_seconds()))
                c=ranked[0]
                reason="date_difference"
                status="candidate_unverified"
            else:
                # Search near the source kickoff for likely team-name aliases; do not auto-join.
                nearby=[c for c in all_db if abs((c[0]-s["kickoff"]).total_seconds()) <= 10*24*3600]
                ranked=[]
                sh,sa=norm(s["home"]),norm(s["away"])
                for c in nearby:
                    dh=difflib.SequenceMatcher(None,sh,norm(c[2])).ratio() if sh else 0
                    da=difflib.SequenceMatcher(None,sa,norm(c[3])).ratio() if sa else 0
                    # Require plausible similarity on both sides; this is only a candidate, never a match.
                    if dh >= 0.72 and da >= 0.72:
                        ranked.append((dh+da,abs((c[0]-s["kickoff"]).total_seconds()),c))
                if ranked:
                    ranked.sort(key=lambda x:(-x[0],x[1]))
                    c=ranked[0][2]
                    reason="team_name_difference"
                    status="candidate_unverified"
                else:
                    c=None
                    # Do not claim a real missing game without a plausible DB candidate.
                    reason="database_missing_or_league_season_coverage"
                    status="manual_review_required"
            unmatched_rows.append({
                "source_league":s["league"], "season":s["season"],
                "source_match_id":s["source_match_id"], "home_team_raw":s["home"],
                "away_team_raw":s["away"], "kickoff_utc":s["kickoff"].isoformat(),
                "candidate_db_match_id":c[1] if c else "",
                "candidate_db_home_team":c[2] if c else "",
                "candidate_db_away_team":c[3] if c else "",
                "candidate_db_kickoff_utc":c[0].isoformat() if c else "",
                "reason_category":reason, "verification_status":status,
                "source_url":s["source_url"]
            })
            continue
        near.sort(key=lambda c:abs((c[0]-s["kickoff"]).total_seconds()))
        bestdiff=abs((near[0][0]-s["kickoff"]).total_seconds())
        best=[c for c in near if abs(abs((c[0]-s["kickoff"]).total_seconds())-bestdiff)<1]
        if len(best)!=1:
            report["ambiguous"]+=1
            continue
        c=best[0]
        matched.append((s,c))
    os.makedirs(os.path.dirname(UNMATCHED_OUT) or ".",exist_ok=True)
    fields=["source_league","season","source_match_id","home_team_raw","away_team_raw",
            "kickoff_utc","candidate_db_match_id","candidate_db_home_team","candidate_db_away_team",
            "candidate_db_kickoff_utc","reason_category","verification_status","source_url"]
    with open(UNMATCHED_OUT,"w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(unmatched_rows)
    report["unmatched_diagnosis_csv"]=UNMATCHED_OUT
    report["unmatched_diagnosis_rows"]=len(unmatched_rows)
    report["unmatched_reason_counts"]={}
    for row in unmatched_rows:
        k=row["reason_category"]
        report["unmatched_reason_counts"][k]=report["unmatched_reason_counts"].get(k,0)+1
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
if __name__ == "__main__":
    main()
    # Reclassify unmatched rows using competition/season metadata before artifacts are uploaded.
    import runpy
    runpy.run_path("data_pipeline/diagnose_xg_unmatched.py", run_name="__main__")
