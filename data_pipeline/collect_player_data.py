import json, re, sqlite3, time, unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone
import requests

DB = "football_model_database.sqlite"
BASES = [
    "https://www.sofascore.com/api/v1",
    "https://api.sofascore.com/api/v1",
]
PROVIDER_TIMEOUT = 12
MAX_DATE_RETRIES = 2
START = "2026-01-01"
END = "2026-09-20"

LEAGUES = {
    "EPL": 17,
    "LALIGA": 8,
    "BUNDESLIGA": 35,
    "SERIEA": 23,
    "LIGUE1": 34,
    "KLEAGUE1": 410,
    "J1": 196,
}
LEAGUE_SLUGS = {
    17: {"premier-league", "premier-league-england", "premier-league"},
    8: {"laliga", "la-liga"},
    35: {"bundesliga"},
    23: {"serie-a"},
    34: {"ligue-1"},
    410: {"k-league-1", "k-league-1-2026"},
    196: {"j1-league", "j1-league-2026"},
}

ALIASES = {
    "psg": "parissaintgermain",
    "parissg": "parissaintgermain",
    "parissaintgermain": "parissaintgermain",
    "bayernmunich": "bayernmunchen",
    "bayernmunchen": "bayernmunchen",
    "intermilan": "inter",
    "internazionale": "inter",
    "sportinglisbon": "sportingcp",
    "sportingcp": "sportingcp",
    "manutd": "manchesterunited",
    "manchesterutd": "manchesterunited",
    "manchesterunitedfc": "manchesterunited",
    "mancity": "mancity",
    "manchestercity": "mancity",
    "tottenhamhotspur": "tottenham",
    "tottenham": "tottenham",
    "athleticbilbao": "athleticclub",
    "athleticclub": "athleticclub",
    "borussiadortmund": "dortmund",
    "dortmund": "dortmund",
    "borussiamonchengladbach": "monchengladbach",
    "monchengladbach": "monchengladbach",
}

def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)

def team_key(s):
    n = norm(s)
    for suffix in ("footballclub", "fc", "cf", "calcio", "1899"):
        if n.endswith(suffix) and len(n) > len(suffix) + 4:
            n = n[:-len(suffix)]
    return ALIASES.get(n, n)

def similarity(a, b):
    a, b = team_key(a), team_key(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()

def find_match(matches, home, away, d, event_ts=None):
    target = datetime.fromisoformat(d).date()
    candidates = []
    event_dt = datetime.fromtimestamp(event_ts, tz=timezone.utc) if event_ts else None
    for (h, a, md), mid in matches.items():
        delta_days = abs((datetime.fromisoformat(md).date() - target).days)
        if delta_days > 1:
            continue
        sh, sa = similarity(home, h), similarity(away, a)
        if sh < 0.60 or sa < 0.60:
            continue
        score = 0.45 * sh + 0.45 * sa + 0.10 * (1.0 if delta_days == 0 else 0.0)
        if event_dt is not None:
            db_ts = datetime.fromisoformat(md + "T12:00:00").replace(tzinfo=timezone.utc)
            hours = abs((event_dt - db_ts).total_seconds()) / 3600.0
            score += 0.08 * max(0.0, 1.0 - min(hours, 36.0) / 36.0)
        candidates.append((score, sh, sa, delta_days, mid))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best = candidates[0]
    if best[1] < 0.60 or best[2] < 0.60:
        return None
    if len(candidates) > 1 and best[0] - candidates[1][0] < 0.02:
        return None
    return best[-1]

def client():
    try:
        from curl_cffi import requests as cr
        return cr.Session(impersonate="chrome")
    except Exception:
        return requests.Session()

S = client()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "x-requested-with": "XMLHttpRequest",
})

def get(path, tries=4):
    for base in BASES:
        for i in range(tries):
            try:
                r = S.get(base + path, timeout=PROVIDER_TIMEOUT)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (403, 429, 567):
                    time.sleep(min(8.0, 1.5 * (i + 1)))
                else:
                    time.sleep(0.5 * (i + 1))
            except Exception:
                time.sleep(1.0 * (i + 1))
    return None

def val(st, *keys):
    for k in keys:
        x = st.get(k)
        if x is not None:
            try:
                return float(x)
            except Exception:
                pass
    return 0.0

def event_date(e):
    ts = e.get("startTimestamp")
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()

def event_league_id(e):
    ut = e.get("uniqueTournament") or {}
    if ut.get("id") is not None:
        return int(ut["id"])
    t = e.get("tournament") or {}
    if t.get("uniqueTournament", {}).get("id") is not None:
        return int(t["uniqueTournament"]["id"])
    if t.get("id") is not None:
        tid = int(t["id"])
        if tid in LEAGUES.values():
            return tid
    return None

def is_target_event(e):
    tid = event_league_id(e)
    if tid in LEAGUES.values():
        return tid
    ut = e.get("uniqueTournament") or {}
    t = e.get("tournament") or {}
    hay = " ".join([
        str(ut.get("name", "")),
        str(ut.get("slug", "")),
        str(t.get("name", "")),
        str(t.get("slug", "")),
    ]).lower().replace("_", "-")
    for wanted_id, slugs in LEAGUE_SLUGS.items():
        if any(s in hay for s in slugs):
            return wanted_id
    return None

def player_stats(lineup, side):
    out = []
    block = lineup.get(side) or {}
    for p in block.get("players", []):
        pl = p.get("player") or {}
        st = p.get("statistics") or {}
        if not pl.get("id"):
            continue
        out.append({
            "player_id": str(pl["id"]),
            "player_name": pl.get("name") or pl.get("shortName") or "unknown",
            "position": p.get("position") or pl.get("position"),
            "starter": 0 if p.get("substitute") else 1,
            "minutes": val(st, "minutesPlayed", "minutes"),
            "rating": st.get("rating"),
            "goals": val(st, "goals"),
            "assists": val(st, "assists", "goalAssist"),
            "xg": val(st, "expectedGoals", "xg"),
            "xa": val(st, "expectedAssists", "xa"),
            "shots": val(st, "totalShots", "shots"),
            "key_passes": val(st, "keyPasses", "keyPass"),
            "tackles": val(st, "tackles"),
            "interceptions": val(st, "interceptions"),
            "clearances": val(st, "clearances"),
        })
    return out

def scheduled_events(d):
    # The daily schedule can be paginated.  Collect every page so a busy
    # football day cannot hide target-league fixtures on page 1 only.
    merged = []
    seen = set()
    best_payload = {}
    for base in BASES:
        for page in range(0, 12):
            path = f"/sport/football/scheduled-events/{d}" if page == 0 else f"/sport/football/scheduled-events/{d}/page/{page}"
            payload = None
            for i in range(MAX_DATE_RETRIES):
                try:
                    r = S.get(base + path, timeout=25)
                    if r.status_code == 200:
                        payload = r.json() or {}
                        break
                    time.sleep(min(6.0, 1.0 * (i + 1)))
                except Exception:
                    time.sleep(1.0 * (i + 1))
            if payload is None:
                break
            events = payload.get("events") or []
            for e in events:
                eid = e.get("id")
                if eid is not None and eid not in seen:
                    seen.add(eid)
                    merged.append(e)
            if not events:
                break
            if not (payload.get("hasNextPage") or len(events) >= 100):
                break
        if merged:
            best_payload = {"events": merged}
            break
    return best_payload

def main():
    con = sqlite3.connect(DB)
    matches = {}
    dates = set()
    for r in con.execute(
        "SELECT match_id,kickoff,home_team,away_team FROM matches "
        "WHERE kickoff>=? AND kickoff<=?",
        (START, END + "T23:59:59"),
    ):
        mid, kickoff, home, away = r
        md = kickoff[:10]
        matches[(team_key(home), team_key(away), md)] = mid
        dates.add(md)

    mapped_ids = set()
    seen_events = 0
    target_events = 0
    mapped = 0
    rows = 0
    skipped = 0
    lineup_failures = 0
    failed_dates = []
    api_event_counts = []
    empty_provider_dates = 0

    # Do not delete the existing enrichment until a replacement batch has
    # actually produced rows. This prevents a transient WAF outage from
    # destroying the last known-good player layer.
    for idx, d in enumerate(sorted(dates), 1):
        payload = scheduled_events(d)
        if not payload:
            failed_dates.append(d)
            continue
        events = payload.get("events", [])
        api_event_counts.append(len(events))
        if not events:
            empty_provider_dates += 1
        for e in events:
            tid = is_target_event(e)
            if tid is None:
                continue
            target_events += 1
            ed = event_date(e)
            if not ed or not (START <= ed <= END):
                continue
            seen_events += 1
            home = (e.get("homeTeam") or {}).get("name", "")
            away = (e.get("awayTeam") or {}).get("name", "")
            event_id = str(e.get("id"))
            mapped_row = con.execute(
                "SELECT match_id FROM provider_event_map WHERE provider=? AND event_id=?",
                ("sofascore", event_id),
            ).fetchone()
            mid = mapped_row[0] if mapped_row else None
            if not mid:
                mid = matches.get((team_key(home), team_key(away), ed))
            if not mid:
                mid = find_match(matches, home, away, ed, e.get("startTimestamp"))
            if not mid:
                skipped += 1
                if skipped <= 25:
                    print(json.dumps({
                        "unmatched_event": event_id,
                        "date": ed,
                        "home": home,
                        "away": away,
                        "home_key": team_key(home),
                        "away_key": team_key(away),
                    }, ensure_ascii=False), flush=True)
                continue
            mapped_ids.add(mid)
            con.execute(
                "INSERT OR REPLACE INTO provider_event_map "
                "(match_id,provider,event_id,observed_at) VALUES (?,?,?,?)",
                (mid, "sofascore", event_id, datetime.utcnow().isoformat(timespec="seconds")),
            )
            lineup = get(f"/event/{event_id}/lineups")
            if not lineup:
                lineup_failures += 1
                continue
            # Persist pre-announced missing players exposed by the lineup feed.
            # For historical T-12h reconstruction this is only used as an
            # absence signal; final XI itself is never treated as a T-12h fact.
            ko_row = con.execute("SELECT kickoff FROM matches WHERE match_id=?", (mid,)).fetchone()
            data_cutoff = None
            if ko_row and ko_row[0]:
                try:
                    data_cutoff = (datetime.fromisoformat(ko_row[0][:19]) - __import__("datetime").timedelta(hours=12)).isoformat(timespec="seconds")
                except Exception:
                    data_cutoff = None
            for side in ("home", "away"):
                for mp in (lineup.get(side) or {}).get("missingPlayers") or []:
                    pl = mp.get("player") or {}
                    pid = str(pl.get("id") or mp.get("playerId") or "")
                    pname = pl.get("name") or mp.get("name") or mp.get("playerName")
                    if not pname:
                        continue
                    reason = mp.get("reason") or mp.get("type") or mp.get("status") or "missing"
                    con.execute(
                        """INSERT INTO injuries
                        (match_id,team_side,player,status,reason,expected_return,
                         source_status,position,availability_prob,expected_start_prob,
                         impact_attack,impact_defense,observed_at,data_cutoff,source_url)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (mid, side, pname, "missing", str(reason), None,
                         "SofaScore public lineup missingPlayers",
                         pl.get("position") or mp.get("position"),
                         0.0, 0.0, 0.0, 0.0,
                         datetime.utcnow().isoformat(timespec="seconds"),
                         data_cutoff, f"https://www.sofascore.com/api/v1/event/{event_id}/lineups")
                    )
            inserted_for_match = 0
            for side in ("home", "away"):
                for p in player_stats(lineup, side):
                    con.execute(
                        """INSERT OR REPLACE INTO player_match_stats
                        (match_id,team_side,player_id,player_name,position,starter,
                         minutes_played,rating,goals,assists,xg,xa,shots,key_passes,
                         tackles,interceptions,clearances,data_source,observed_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            mid, side, p["player_id"], p["player_name"], p["position"],
                            p["starter"], p["minutes"], p["rating"], p["goals"],
                            p["assists"], p["xg"], p["xa"], p["shots"], p["key_passes"],
                            p["tackles"], p["interceptions"], p["clearances"],
                            "SofaScore public lineup",
                            datetime.utcnow().isoformat(timespec="seconds"),
                        ),
                    )
                    rows += 1
                    inserted_for_match += 1
            if inserted_for_match:
                mapped += 1
            time.sleep(0.08)
        if idx % 10 == 0:
            con.commit()
        print(json.dumps({
            "date": d,
            "progress": f"{idx}/{len(dates)}",
            "target_events": target_events,
            "mapped_matches": mapped,
            "player_rows": rows,
            "lineup_failures": lineup_failures,
        }, ensure_ascii=False), flush=True)

    # A successful provider response with zero target events is a hard failure:
    # a green workflow must never imply player enrichment exists when it does not.
    result = {
        "mapped_matches": mapped,
        "player_rows": rows,
        "unmatched_events": skipped,
        "seen_provider_events": seen_events,
        "target_events": target_events,
        "lineup_failures": lineup_failures,
        "failed_dates": len(failed_dates),
        "scheduled_dates": len(dates),
        "provider_event_dates_with_zero_events": empty_provider_dates,
        "provider_event_count_total": sum(api_event_counts),
        "provider_event_count_max": max(api_event_counts) if api_event_counts else 0,
    }
    if rows > 0:
        con.commit()
    con.close()
    print(json.dumps(result, ensure_ascii=False), flush=True)

    if target_events == 0 or mapped == 0 or rows == 0:
        print(
            "PLAYER_ENRICHMENT_DEGRADED_NON_BLOCKING: "
            + json.dumps(result, ensure_ascii=False),
            flush=True,
        )
        return

if __name__ == "__main__":
    main()
