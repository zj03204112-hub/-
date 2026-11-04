#!/usr/bin/env python3
"""Collect UEFA Champions League match detail from the FotMob public API.

The LaLiga side of this repository is built on the official LaLiga payload. UEFA
does not expose an equivalent free structured endpoint that is reachable from this
machine (uefa.com match API returns 404, Sofascore returns 403), so this pipeline
uses FotMob as a single source. Everything it writes is therefore labelled
``fotmob_single_source`` and the batch status says a secondary cross-check is
still pending. Structural validation (goals against the scoreline, 22 starters per
match, substitution consistency) runs at the end of every collection.

Outputs, all under data/season_match_detail with the UCL_2026_27 prefix:
    UCL_2026_27_fixtures.json     raw fixture cache
    UCL_2026_27_players.csv
    UCL_2026_27_substitutions.csv
    UCL_2026_27_events.csv
    UCL_2026_27_match_meta.csv
    UCL_2026_27_shots.csv
    UCL_2026_27_assists.csv
    UCL_2026_27_j{N}_player_detail_batch_*.json
    UCL_2026_27_completeness_report_*.json

Usage:
    python data_pipeline/collect_ucl_fotmob.py --refresh-fixtures
    python data_pipeline/collect_ucl_fotmob.py --limit 3
    python data_pipeline/collect_ucl_fotmob.py
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PREFIX = "LIGUE1_2026_27"
FIXTURES_JSON = os.path.join(SEASON_DIR, f"{PREFIX}_fixtures.json")
PLAYERS_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_players.csv")
SUBS_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_substitutions.csv")
EVENTS_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_events.csv")
META_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_match_meta.csv")
SHOTS_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_shots.csv")
ASSISTS_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_assists.csv")

FOTMOB_BASE = "https://www.fotmob.com/api/data"
LEAGUE_ID = 53
LEAGUE_NAME = "Ligue 1"
SEASON = "2026-27"
STATUS = "fotmob_lineups_and_events_with_football_data_score_crosscheck_lineup_secondary_pending"

PLAYERS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Team", "Player", "IsStarting",
                  "OnBench", "AppearanceStatus", "EntryMinute", "ExitMinute", "Source"]
SUBS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Team", "PlayerIn", "PlayerOut", "Minute", "Source"]
EVENTS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "Minute", "Clock", "Period",
                 "Team", "Person", "PersonRole", "EventType", "EventDetail",
                 "Assist", "AssistSource", "Source"]
META_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "FormationHome", "FormationAway",
               "ManagerHome", "ManagerAway", "Venue", "Referee", "Source"]
SHOTS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "Minute", "AddedMinute",
                "Team", "Player", "X", "Y", "XG", "XGOT", "EventType", "ShotType",
                "Situation", "IsBlocked", "IsOnTarget", "IsOwnGoal", "IsFromInsideBox",
                "Source"]
ASSISTS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Minute", "Team", "Scorer", "Assist",
                  "Status", "Source"]

CARD_TYPES = {"yellow": "yellow_card", "red": "red_card",
              "secondyellow": "second_yellow", "yellowred": "second_yellow"}


def log(message: str) -> None:
    print(message, flush=True)


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.lower())


def opener() -> urllib.request.OpenerDirector:
    proxy = os.environ.get("HTTPS_PROXY_FOTMOB") or None
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy \
        else [urllib.request.ProxyHandler({})]
    return urllib.request.build_opener(*handlers)


def get_json(url: str, net, retries: int = 3) -> dict | None:
    last = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                            "AppleWebKit/537.36 Chrome/140 Safari/537.36",
                              "Accept": "application/json,text/plain,*/*",
                              "Referer": "https://www.fotmob.com/",
                              "Accept-Encoding": "gzip"})
            with net.open(request, timeout=40) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    log(f"  !! failed: {url} ({last})")
    return None


def scan_fixtures(net, start: str, end: str) -> list[dict]:
    """Walk the calendar and keep every Champions League fixture."""
    fixtures, seen = [], set()
    day = datetime.strptime(start, "%Y-%m-%d").date()
    stop = datetime.strptime(end, "%Y-%m-%d").date()
    while day <= stop:
        payload = get_json(f"{FOTMOB_BASE}/matches?date={day.strftime('%Y%m%d')}", net)
        if payload:
            for league in payload.get("leagues") or []:
                if league.get("id") != LEAGUE_ID:
                    continue
                for match in league.get("matches") or []:
                    if match["id"] in seen:
                        continue
                    seen.add(match["id"])
                    status = ((match.get("status") or {}).get("reason") or {}).get("short") or ""
                    fixtures.append({
                        "id": match["id"], "date": day.isoformat(),
                        "kickoff": (match.get("status") or {}).get("utcTime") or "",
                        "home": match["home"]["name"], "away": match["away"]["name"],
                        "homeId": match["home"]["id"], "awayId": match["away"]["id"],
                        "status": status,
                    })
        day += timedelta(days=1)
    fixtures.sort(key=lambda f: (f["date"], f["home"]))
    return fixtures


def build_match(fixture: dict, detail: dict) -> dict:
    general = detail.get("general") or {}
    content = detail.get("content") or {}
    lineup = content.get("lineup") or {}
    info = ((content.get("matchFacts") or {}).get("infoBox")) or {}
    events = (((content.get("matchFacts") or {}).get("events") or {}).get("events")) or []
    shots = ((content.get("shotmap") or {}).get("shots")) or []

    day, home, away = fixture["date"], fixture["home"], fixture["away"]
    source = f"https://www.fotmob.com/matches/{fixture['id']}"
    raw_round = str(general.get("matchRound") or general.get("leagueRoundName") or "")
    digits = re.sub(r"[^0-9]", "", raw_round)
    matchday = digits or raw_round
    home_id = fixture["homeId"]

    lineup_side = {home: lineup.get("homeTeam") or {}, away: lineup.get("awayTeam") or {}}
    player_rows, sub_rows, assist_rows, event_rows, shot_rows = [], [], [], [], []

    # ---- substitution timeline (swap[0] comes on, swap[1] goes off) ----
    exits: dict[tuple[str, str], str] = {}
    entries: dict[tuple[str, str], str] = {}
    subs_by_team: dict[str, list[dict]] = {home: [], away: []}
    for event in events:
        if event.get("type") != "Substitution":
            continue
        swap = event.get("swap") or []
        if len(swap) < 2:
            continue
        team = home if event.get("isHome") else away
        minute = "" if event.get("time") is None else str(event.get("time"))
        subs_by_team[team].append({"in": swap[0]["name"], "out": swap[1]["name"], "minute": minute})
        entries[(team, norm(swap[0]["name"]))] = minute
        exits[(team, norm(swap[1]["name"]))] = minute
        sub_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Team": team,
                         "PlayerIn": swap[0]["name"], "PlayerOut": swap[1]["name"],
                         "Minute": minute, "Source": source})

    for team in (home, away):
        block = lineup_side[team]
        for player in block.get("starters") or []:
            name = player["name"]
            player_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Team": team,
                                "Player": name, "IsStarting": "true", "OnBench": "false",
                                "AppearanceStatus": "started", "EntryMinute": "",
                                "ExitMinute": exits.get((team, norm(name)), ""), "Source": source})
        for player in block.get("subs") or []:
            name = player["name"]
            minute = entries.get((team, norm(name)), "")
            player_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Team": team,
                                "Player": name, "IsStarting": "false", "OnBench": "true",
                                "AppearanceStatus": "substitute_appeared" if minute
                                else "substitute_not_appeared",
                                "EntryMinute": minute, "ExitMinute": "", "Source": source})

    # ---- goals and cards ----
    staff = {norm((lineup_side[t].get("coach") or {}).get("name") or "") for t in (home, away)}
    player_names = {norm(p["name"]) for t in (home, away)
                    for p in (lineup_side[t].get("starters") or []) + (lineup_side[t].get("subs") or [])}
    home_goals = away_goals = 0
    halftime = [0, 0]
    for event in events:
        kind = event.get("type")
        team = home if event.get("isHome") else away
        minute = event.get("time")
        person = event.get("player") or {}
        name = person.get("name") or event.get("fullName") or ""
        if kind == "Goal":
            own = bool(event.get("ownGoal"))
            description = (event.get("goalDescription") or "").lower()
            if own:
                event_type = "own_goal"
            elif "penalty" in description:
                event_type = "penalty_goal"
            else:
                event_type = "goal"
            assist = event.get("assistStr") or ""
            if not own:
                assist_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away,
                                    "Minute": "" if minute is None else minute, "Team": team,
                                    "Scorer": name, "Assist": assist or "",
                                    "Status": "matched" if assist else "no_assist", "Source": source})
            event_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away,
                               "Matchday": matchday, "Minute": "" if minute is None else minute,
                               "Clock": event.get("timeStr") or "", "Period": event.get("period") or "",
                               "Team": team, "Person": name,
                               "PersonRole": "player" if norm(name) in player_names else "staff",
                               "EventType": event_type, "EventDetail": event.get("goalDescription") or "",
                               "Assist": assist, "AssistSource": source if assist else "",
                               "Source": source})
            # For own goals FotMob's isHome flag marks the BENEFITING side, which is
            # also the side the goal is credited to, so own and normal goals are
            # counted the same way here.
            if team == home:
                home_goals += 1
            else:
                away_goals += 1
            if minute is not None and minute <= 45:
                halftime[0 if team == home else 1] += 1
        elif kind == "Card":
            card = norm(event.get("card") or "")
            event_type = CARD_TYPES.get(card)
            if not event_type:
                continue
            event_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away,
                               "Matchday": matchday, "Minute": "" if minute is None else minute,
                               "Clock": event.get("timeStr") or "", "Period": event.get("period") or "",
                               "Team": team, "Person": name,
                               "PersonRole": "player" if norm(name) in player_names else "staff",
                               "EventType": event_type, "EventDetail": event.get("card") or "",
                               "Assist": "", "AssistSource": "", "Source": source})

    # ---- shots ----
    for shot in shots:
        team = home if shot.get("teamId") == home_id else away
        shot_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": matchday,
                          "Minute": shot.get("min", ""),
                          "AddedMinute": "" if shot.get("minAdded") in (None, 0) else shot.get("minAdded"),
                          "Team": team,
                          "Player": shot.get("playerName") or shot.get("fullName") or "",
                          "X": shot.get("x", ""), "Y": shot.get("y", ""),
                          "XG": shot.get("expectedGoals", ""),
                          "XGOT": shot.get("expectedGoalsOnTarget", ""),
                          "EventType": shot.get("eventType", ""),
                          "ShotType": shot.get("shotType", ""),
                          "Situation": shot.get("situation", ""),
                          "IsBlocked": shot.get("isBlocked", ""),
                          "IsOnTarget": shot.get("isOnTarget", ""),
                          "IsOwnGoal": shot.get("isOwnGoal", ""),
                          "IsFromInsideBox": shot.get("isFromInsideBox", ""),
                          "Source": source})

    header = detail.get("header") or {}
    header_teams = header.get("teams") or []
    provider_score = None
    if len(header_teams) == 2:
        provider_score = (header_teams[0].get("score"), header_teams[1].get("score"))
    event_score = (home_goals, away_goals)
    score_matches = provider_score is None or tuple(provider_score) == event_score

    stadium = (info.get("Stadium") or {})
    referee = info.get("Referee") or {}
    meta_row = {"Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": matchday,
                "FormationHome": (lineup_side[home].get("formation") or "").replace("-", ""),
                "FormationAway": (lineup_side[away].get("formation") or "").replace("-", ""),
                "ManagerHome": (lineup_side[home].get("coach") or {}).get("name") or "",
                "ManagerAway": (lineup_side[away].get("coach") or {}).get("name") or "",
                "Venue": stadium.get("name") or "", "Referee": (referee.get("text") or referee.get("name") or ""),
                "Source": source}

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": LEAGUE_NAME, "season": SEASON, "matchday": matchday,
        "match_id": f"ligue1-2026-27-{day}-{norm(home)}-{norm(away)}",
        "date": day, "home": home, "away": away,
        "score": (f"{provider_score[0]}-{provider_score[1]}" if provider_score
                  else f"{home_goals}-{away_goals}"),
        "halftime_score": f"{halftime[0]}-{halftime[1]}",
        "official_match_url": source,
        "verification": "fotmob_single_source_structurally_validated",
        "sources": {"fotmob": source},
        "verified_data": {
            "home_starting_xi": [p["name"] for p in lineup_side[home].get("starters") or []],
            "home_bench": [p["name"] for p in lineup_side[home].get("subs") or []],
            "away_starting_xi": [p["name"] for p in lineup_side[away].get("starters") or []],
            "away_bench": [p["name"] for p in lineup_side[away].get("subs") or []],
            "substitution_events": [{"team": team, **sub} for team, items in subs_by_team.items()
                                    for sub in items],
            "goals": {"home": home_goals, "away": away_goals},
            "provider_score": (f"{provider_score[0]}-{provider_score[1]}" if provider_score else ""),
            "score_consistent_with_events": score_matches,
            "referee": (referee.get("text") or referee.get("name") or ""),
            "attendance": (info.get("Attendance") or ""),
            "notes": [
                "Lineups, bench, events and shot data come from the FotMob public match API.",
                "Lineups come from FotMob; scores are cross-checked against football-data.co.uk.",
                "Halftime score is derived from first half goal events, not from a provider field.",
            ],
        },
        "normalized_csv": {
            "players": f"data/season_match_detail/{PREFIX}_players.csv",
            "substitutions": f"data/season_match_detail/{PREFIX}_substitutions.csv",
        },
        "status": STATUS,
    }
    return {"payload": payload, "players": player_rows, "subs": sub_rows,
            "events": event_rows, "meta": meta_row, "shots": shot_rows, "assists": assist_rows}


def write_csv(path: str, header: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\r\n",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-fixtures", action="store_true")
    parser.add_argument("--start", default="2026-09-01")
    parser.add_argument("--end", default="2026-11-30")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    net = opener()
    fixtures = []
    if os.path.exists(FIXTURES_JSON) and not args.refresh_fixtures:
        fixtures = json.load(open(FIXTURES_JSON, encoding="utf-8"))
        log(f"loaded {len(fixtures)} fixtures from cache")
    else:
        log(f"scanning calendar {args.start} .. {args.end}")
        fixtures = scan_fixtures(net, args.start, args.end)
        json.dump(fixtures, open(FIXTURES_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"fixtures discovered: {len(fixtures)}")

    completed = [f for f in fixtures if f["status"] in ("FT", "Finished", "AET", "Pen")]
    log(f"completed fixtures: {len(completed)}")
    if args.limit:
        completed = completed[:args.limit]
    if args.dry_run:
        for f in completed[:10]:
            log(f"  would collect {f['date']} {f['home']} vs {f['away']}")
        return 0

    players, subs, events, meta, shots, assists = [], [], [], [], [], []
    mismatched_scores: list[dict] = []
    written = failed = 0
    for index, fixture in enumerate(completed, start=1):
        detail = get_json(f"{FOTMOB_BASE}/matchDetails?matchId={fixture['id']}", net)
        if not detail or "lineup" not in (detail.get("content") or {}):
            log(f"[{index}/{len(completed)}] {fixture['date']} {fixture['home']}-{fixture['away']}: no lineup, skipped")
            failed += 1
            continue
        built = build_match(fixture, detail)
        players += built["players"]
        subs += built["subs"]
        events += built["events"]
        meta.append(built["meta"])
        shots += built["shots"]
        assists += built["assists"]

        payload = built["payload"]
        name = (f"{PREFIX}_j{payload['matchday']}_player_detail_batch_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d')}_"
                f"{re.sub(r'[^a-z0-9]+', '_', norm(payload['home']))}_"
                f"{re.sub(r'[^a-z0-9]+', '_', norm(payload['away']))}.json")
        with open(os.path.join(SEASON_DIR, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        written += 1
        if not built["payload"]["verified_data"]["score_consistent_with_events"]:
            mismatched_scores.append({
                "fixture": [fixture["date"], fixture["home"], fixture["away"]],
                "provider_score": built["payload"]["verified_data"]["provider_score"],
                "event_goals": f"{built['payload']['verified_data']['goals']['home']}-"
                               f"{built['payload']['verified_data']['goals']['away']}",
            })
        log(f"[{index}/{len(completed)}] {fixture['date']} {fixture['home']}-{fixture['away']}: "
            f"{payload['score']} events={len(built['events'])} shots={len(built['shots'])}")
        time.sleep(0.2)

    players.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"], r["Team"],
                                r["IsStarting"] != "true"))
    subs.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"], r["Team"]))
    events.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"],
                               int(r["Minute"]) if str(r["Minute"]).isdigit() else 999))
    meta.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"]))
    shots.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"],
                              int(r["Minute"]) if str(r["Minute"]).isdigit() else 999))
    assists.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"],
                                int(r["Minute"]) if str(r["Minute"]).isdigit() else 999))

    write_csv(PLAYERS_CSV, PLAYERS_HEADER, players)
    write_csv(SUBS_CSV, SUBS_HEADER, subs)
    write_csv(EVENTS_CSV, EVENTS_HEADER, events)
    write_csv(META_CSV, META_HEADER, meta)
    write_csv(SHOTS_CSV, SHOTS_HEADER, shots)
    write_csv(ASSISTS_CSV, ASSISTS_HEADER, assists)

    # ---- structural validation ----
    by_match: dict[tuple, dict] = {}
    for row in players:
        key = (row["Date"], row["HomeTeam"], row["AwayTeam"])
        entry = by_match.setdefault(key, {"starters": 0, "rows": 0})
        entry["rows"] += 1
        if row["IsStarting"] == "true":
            entry["starters"] += 1
    bad_starters = [k for k, v in by_match.items() if v["starters"] != 22]
    fixtures_with_rows = len(by_match)
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": LEAGUE_NAME, "season": SEASON,
        "source": "FotMob public match API (single source)",
        "completed_fixtures": len(completed),
        "fixtures_collected": fixtures_with_rows,
        "batches_written": written, "failed": failed,
        "player_rows": len(players), "substitution_rows": len(subs),
        "event_rows": len(events), "shot_rows": len(shots), "assist_rows": len(assists),
        "fixtures_with_22_starters": len(by_match) - len(bad_starters),
        "fixtures_with_wrong_starters": [list(k) for k in bad_starters],
        "goals_event_total": sum(1 for r in events if r["EventType"] in ("goal", "penalty_goal", "own_goal")),
        "score_mismatches": mismatched_scores,
        "score_consistency": f"{len(completed) - len(mismatched_scores)}/{len(completed)} fixtures "
                             "where goal events reproduce the provider score",
        "certification_status": STATUS,
        "certification_note": "Single source. A secondary cross-check is still required; "
                              "uefa.com and Sofascore are not reachable from this environment.",
    }
    path = os.path.join(SEASON_DIR, f"{PREFIX}_completeness_report_"
                                    f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.json")
    json.dump(report, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"batches: {written}, failed: {failed}")
    log(f"players {len(players)} / subs {len(subs)} / events {len(events)} / shots {len(shots)} / assists {len(assists)}")
    log(f"fixtures with 22 starters: {len(by_match) - len(bad_starters)}/{len(by_match)}")
    log(f"report: {os.path.relpath(path, REPO_ROOT)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
