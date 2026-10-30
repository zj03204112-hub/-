import csv, json, time, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests

BASE = "https://understat.com/"
HEADERS = {"User-Agent":"Mozilla/5.0","X-Requested-With":"XMLHttpRequest","Referer":BASE,"Accept":"application/json, text/javascript, */*; q=0.01"}
CACHE = Path("data/season_match_detail_cache/understat")
OUT = Path("data/season_match_detail")
CACHE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

# Explicit source-name aliases; canonicalization below also removes common club suffixes.
ALIASES = {
    "liverpool fc":"liverpool", "afc bournemouth":"bournemouth",
    "brighton and hove albion fc":"brighton", "brighton hove albion":"brighton",
    "manchester city fc":"manchester city", "manchester united fc":"manchester united",
    "tottenham hotspur fc":"tottenham", "tottenham hotspur":"tottenham",
    "west ham united fc":"west ham", "west ham united":"west ham",
    "newcastle united fc":"newcastle", "newcastle united":"newcastle",
    "nottingham forest fc":"nottingham forest", "wolverhampton wanderers fc":"wolves",
    "wolverhampton wanderers":"wolves", "wolves":"wolves",
    "aston villa fc":"aston villa", "fulham fc":"fulham", "brentford fc":"brentford",
    "chelsea fc":"chelsea", "arsenal fc":"arsenal", "crystal palace fc":"crystal palace",
    "sunderland afc":"sunderland", "leeds united fc":"leeds", "leeds united":"leeds",
    "burnley fc":"burnley", "everton fc":"everton",
}
def get(path, name):
    p = CACHE / name
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = requests.get(BASE + path.lstrip("/"), headers=HEADERS, timeout=45)
    r.raise_for_status()
    data = r.json()
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    time.sleep(1.1)
    return data

def canonical(name):
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if s in ALIASES:
        return ALIASES[s]
    for suffix in (" football club", " fc", " afc"):
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
    return ALIASES.get(s, s)

def parse_date(value):
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        # Understat usually supplies YYYY-MM-DD HH:MM:SS without a timezone.
        dt = datetime.fromisoformat(s)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

def num(value):
    try:
        return float(value) if value not in (None, "", "null") else None
    except (ValueError, TypeError):
        return None

src = OUT / "premier-league_2025_openfootball_results.csv"
with src.open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
league = get("getLeagueData/EPL/2025", "EPL_2025_league.json")
dates = league.get("dates", [])

# Index by normalized home/away names first, then allow a one-day date shift only
# when team pair is unique (timezone/civil-date boundary handling).
by_teams = {}
for m in dates:
    h, a = m.get("h") or {}, m.get("a") or {}
    key = (canonical(h.get("title")), canonical(a.get("title")))
    by_teams.setdefault(key, []).append(m)

errors, match_audit = [], []
for i, row in enumerate(rows, 1):
    hname, aname = canonical(row.get("home_team")), canonical(row.get("away_team"))
    key = (hname, aname)
    target_date = parse_date(row.get("date"))
    candidates = by_teams.get(key, [])
    scored = []
    for m in candidates:
        md = parse_date(m.get("datetime") or m.get("date"))
        if not md or not target_date:
            continue
        day_delta = abs((md - target_date).days)
        if day_delta <= 1:
            scored.append((day_delta, m, md))
    scored.sort(key=lambda x: x[0])
    chosen = None
    status = "unmatched_team_pair"
    if len(scored) == 1:
        chosen = scored[0][1]
        status = "matched_exact_date" if scored[0][0] == 0 else "matched_date_shift_1d"
    elif len(scored) > 1:
        best_delta = scored[0][0]
        best = [x for x in scored if x[0] == best_delta]
        if len(best) == 1:
            chosen = best[0][1]
            status = "matched_exact_date" if best_delta == 0 else "matched_date_shift_1d"
        else:
            status = "ambiguous_multiple_candidates"

    row.update({
        "understat_match_id":"", "understat_home_team":"", "understat_away_team":"",
        "understat_match_date":"", "understat_match_status":status,
        "home_xg_understat":"", "away_xg_understat":"", "home_xga_understat":"",
        "away_xga_understat":"", "home_roster_understat":"", "away_roster_understat":"",
        "verified_starting_xi":"no", "verified_actual_appearances":"no"
    })
    if not chosen:
        match_audit.append({"row":i,"date":row.get("date"),"home":row.get("home_team"),"away":row.get("away_team"),"status":status,"candidate_count":len(scored)})
        continue

    mid = str(chosen.get("id") or chosen.get("match_id") or "")
    mh, ma = chosen.get("h") or {}, chosen.get("a") or {}
    md = parse_date(chosen.get("datetime") or chosen.get("date"))
    row.update({"understat_match_id":mid,"understat_home_team":mh.get("title",""),
                "understat_away_team":ma.get("title",""),"understat_match_date":md.isoformat() if md else "",
                "understat_match_status":status})
    # League endpoint already contains match-level team xG; prefer it over re-summing shots.
    xg = chosen.get("xG") or {}
    hxg, axg = num(xg.get("h")), num(xg.get("a"))
    if hxg is not None and axg is not None:
        row.update({"home_xg_understat":round(hxg,4),"away_xg_understat":round(axg,4),
                    "home_xga_understat":round(axg,4),"away_xga_understat":round(hxg,4)})
    else:
        row["understat_match_status"] += "|xg_missing"
    # Keep raw roster evidence; do not infer starting XI or appearances from it.
    if mid:
        try:
            detail = get("getMatchData/" + mid, "match_" + mid + ".json")
            rosters = detail.get("rosters") or {}
            if rosters.get("h") is not None:
                row["home_roster_understat"] = json.dumps(rosters["h"], ensure_ascii=False)
            if rosters.get("a") is not None:
                row["away_roster_understat"] = json.dumps(rosters["a"], ensure_ascii=False)
        except Exception as e:
            errors.append({"match_id":mid,"error":str(e)})
            row["understat_match_status"] += "|roster_fetch_error"
    match_audit.append({"row":i,"date":row.get("date"),"home":row.get("home_team"),"away":row.get("away_team"),"status":row["understat_match_status"],"understat_match_id":mid,"understat_date":row.get("understat_match_date")})
    if i % 25 == 0:
        print(f"Processed {i}/{len(rows)}", flush=True)

out = OUT / "premier-league_2025_enriched.csv"
cols = list(dict.fromkeys(k for r in rows for k in r))
with out.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=cols)
    writer.writeheader()
    writer.writerows(rows)

def has(row, key):
    return row.get(key) not in (None, "", "null")
n = len(rows)
matched = sum(has(r, "understat_match_id") for r in rows)
xg_rows = sum(has(r,"home_xg_understat") and has(r,"away_xg_understat") for r in rows)
roster_rows = sum(has(r,"home_roster_understat") and has(r,"away_roster_understat") for r in rows)
report = {
    "source":"Understat free public data",
    "source_docs":"https://github.com/withqwerty/football-docs/blob/main/docs/free-sources/understat.md",
    "season":"2025-26","collected_at_utc":datetime.now(timezone.utc).isoformat(),
    "baseline_rows":n,"understat_league_match_records":len(dates),
    "matched_rows":matched,"unmatched_rows":n-matched,
    "match_id_coverage_pct":round(matched*100/n,2) if n else 0,
    "both_team_xg_rows":xg_rows,"both_team_xg_coverage_pct":round(xg_rows*100/n,2) if n else 0,
    "both_raw_roster_rows":roster_rows,"both_raw_roster_coverage_pct":round(roster_rows*100/n,2) if n else 0,
    "verified_starting_xi_rows":0,"verified_actual_appearance_rows":0,
    "status_counts":{s:sum(1 for r in rows if s in (r.get("understat_match_status") or "")) for s in sorted(set(r.get("understat_match_status","") for r in rows))},
    "match_level_audit":match_audit,
    "notes":["Team xG/xGA are taken from Understat league match record when both values exist.",
             "Team names are normalized through an explicit alias table; one-day date shifts are accepted only for a unique home/away pair.",
             "Ambiguous and unmatched records remain blank; no scores or xG values are imputed.",
             "Raw rosters are retained but are not treated as verified starting XI or actual appearances.",
             "Understat match IDs are source-specific."],
    "fetch_errors":errors
}
(OUT/"premier-league_2025_enriched_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k!="match_level_audit"},ensure_ascii=False,indent=2))
if matched == 0:
    raise SystemExit("No matches joined; audit failed")
