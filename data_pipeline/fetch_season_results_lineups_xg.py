#!/usr/bin/env python3
"""Fetch auditable match-level results, actual lineups, and xG/xGA from API-Football.
Run one league/season per job to control API quotas. Never fabricate missing fields.
"""
from __future__ import annotations
import csv, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests

KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
BASE = "https://v3.football.api-sports.io"
LEAGUES = {
    "premier-league": (39, "Premier League"),
    "la-liga": (140, "LaLiga"),
    "bundesliga": (78, "Bundesliga"),
    "serie-a": (135, "Serie A"),
    "ligue-1": (61, "Ligue 1"),
    "j1": (98, "J1 League"),
    "kleague1": (292, "K League 1"),
}
OUT = Path("data/season_match_detail")
CACHE = Path("data/season_match_detail_cache")
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update({"x-apisports-key": KEY, "User-Agent": "football-season-audit/1.0"})

def api(endpoint, params):
    if not KEY:
        raise RuntimeError("API_FOOTBALL_KEY repository secret is not configured")
    url = f"{BASE}/{endpoint}"
    for attempt in range(5):
        r = SESSION.get(url, params=params, timeout=40)
        if r.status_code == 429:
            wait = min(60, 5 * (attempt + 1))
            print(f"Rate limited; waiting {wait}s", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        payload = r.json()
        if payload.get("errors"):
            raise RuntimeError(f"{endpoint} {params}: {payload['errors']}")
        return payload
    raise RuntimeError(f"Rate limit persisted for {endpoint} {params}")

def cached_json(path, fetcher):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            path.unlink(missing_ok=True)
    data = fetcher()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data

def get_stat(stats, key):
    for item in stats or []:
        if str(item.get("type", "")).strip().lower() == key.lower():
            return item.get("value")
    return None

def main():
    league_key = os.getenv("LEAGUE_KEY", "premier-league").strip()
    season = int(os.getenv("SEASON", "2025"))
    if league_key not in LEAGUES:
        raise SystemExit(f"Unsupported LEAGUE_KEY={league_key}; options: {', '.join(LEAGUES)}")
    league_id, league_name = LEAGUES[league_key]
    stem = f"{league_key}_{season}"
    fixtures_path = CACHE / f"{stem}_fixtures.json"
    fixtures_payload = cached_json(fixtures_path, lambda: api("fixtures", {"league": league_id, "season": season}))
    fixtures = fixtures_payload.get("response", [])
    if not fixtures:
        raise SystemExit(f"No fixtures returned for {league_name} season {season}; verify provider plan/season coverage.")
    rows = []
    player_rows = []
    substitution_rows = []
    errors = []
    for n, item in enumerate(fixtures, 1):
        fixture = item.get("fixture", {})
        status = (fixture.get("status") or {}).get("short")
        score = item.get("score") or {}
        fulltime = score.get("fulltime") or {}
        teams = item.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        # Keep all scheduled fixtures in the audit; actual score/result only for completed matches.
        completed = status in {"FT", "AET", "PEN"}
        hg, ag = (fulltime.get("home"), fulltime.get("away")) if completed else (None, None)
        outcome = ("H" if hg > ag else "D" if hg == ag else "A") if hg is not None and ag is not None else None
        row = {
            "fixture_id": fixture.get("id"), "league": league_name, "league_id": league_id,
            "season_start_year": season, "kickoff_utc": fixture.get("date"), "status": status,
            "home_team": home.get("name"), "away_team": away.get("name"),
            "home_goals_90m": hg, "away_goals_90m": ag, "result_90m": outcome,
            "home_starting_xi": None, "away_starting_xi": None,
            "home_players_appeared": None, "away_players_appeared": None,
            "home_xg": None, "away_xg": None, "home_xga": None, "away_xga": None,
            "source": "API-Football", "source_fixture_id": fixture.get("id"),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "lineup_status": "not_applicable_unfinished" if not completed else "not_retrieved",
            "xg_status": "not_applicable_unfinished" if not completed else "not_retrieved",
            "substitution_events_status": "not_applicable_unfinished" if not completed else "not_retrieved",
        }
        if completed:
            fid = fixture.get("id")
            # Normalized per-player records; listing as a substitute is not treated as an appearance.
            fixture_player_rows = {}
            team_info = {home.get("id"): (home.get("name"), "home"), away.get("id"): (away.get("name"), "away")}
            try:
                lineup_file = CACHE / f"fixture_{fid}_lineups.json"
                lineup_data = cached_json(lineup_file, lambda fid=fid: api("fixtures/lineups", {"fixture": fid}))
                lineup_response = lineup_data.get("response", [])
                for lineup in lineup_response:
                    team = (lineup.get("team") or {}).get("id")
                    team_name = (lineup.get("team") or {}).get("name")
                    side = team_info.get(team, (team_name, "unknown"))[1]
                    start = lineup.get("startXI") or []
                    subs = lineup.get("substitutes") or []
                    starters = [p.get("player", {}).get("name") for p in start if p.get("player", {}).get("name")]
                    if team == home.get("id"):
                        row["home_starting_xi"] = starters
                    elif team == away.get("id"):
                        row["away_starting_xi"] = starters
                    else:
                        errors.append({"fixture_id": fid, "lineup_error": f"Unexpected team ID in lineup response: {team}"})
                    # Keep both starters and listed substitutes; actual minutes are joined from fixtures/players.
                    for is_starting, entries in ((True, start), (False, subs)):
                        for entry in entries:
                            p = entry.get("player") or {}
                            name = p.get("name")
                            pid = p.get("id")
                            if not name:
                                continue
                            pkey = (str(team), str(pid) if pid is not None else name)
                            fixture_player_rows[pkey] = {
                                "fixture_id": fid, "league": league_name, "season_start_year": season,
                                "kickoff_utc": fixture.get("date"), "home_team": home.get("name"),
                                "away_team": away.get("name"), "team_id": team, "team_name": team_name,
                                "team_side": side, "player_id": pid, "player_name": name,
                                "shirt_number": p.get("number"), "position": p.get("pos"),
                                "lineup_role": "starter" if is_starting else "listed_substitute",
                                "is_starting": is_starting, "is_listed_substitute": not is_starting,
                                "minutes_played": None, "appeared": None,
                                "lineup_source": "API-Football fixtures/lineups",
                                "player_stats_source": "API-Football fixtures/players",
                                "retrieved_at_utc": row["retrieved_at_utc"]
                            }
                stat_file = CACHE / f"fixture_{fid}_statistics.json"
                stat_data = cached_json(stat_file, lambda fid=fid: api("fixtures/statistics", {"fixture": fid}))
                stat_response = stat_data.get("response", [])
                xgs = {}
                for team_stats in stat_response:
                    tid = (team_stats.get("team") or {}).get("id")
                    xg = get_stat(team_stats.get("statistics"), "expected_goals")
                    xgs[tid] = xg
                row["home_xg"] = xgs.get(home.get("id"))
                row["away_xg"] = xgs.get(away.get("id"))
                # xGA is the opponent's xG for the same fixture; keep explicit rather than infer from goals.
                row["home_xga"] = row["away_xg"]
                row["away_xga"] = row["home_xg"]
                row["lineup_status"] = "ok" if len(row["home_starting_xi"] or []) == 11 and len(row["away_starting_xi"] or []) == 11 else "missing_or_incomplete_lineup"
                row["xg_status"] = "ok" if row["home_xg"] is not None and row["away_xg"] is not None else "missing_xg"
            except Exception as exc:
                errors.append({"fixture_id": fid, "lineup_or_xg_error": str(exc)})
                row["lineup_status"] = "ok" if len(row["home_starting_xi"] or []) == 11 and len(row["away_starting_xi"] or []) == 11 else "fetch_error_or_incomplete_lineup"
                row["xg_status"] = "ok" if row["home_xg"] is not None and row["away_xg"] is not None else "fetch_error_or_missing_xg"
            # API-Football player statistics are a separate request and required to identify subs who actually entered.
            try:
                player_file = CACHE / f"fixture_{fid}_players.json"
                player_data = cached_json(player_file, lambda fid=fid: api("fixtures/players", {"fixture": fid}))
                for team_block in player_data.get("response", []):
                    tid = (team_block.get("team") or {}).get("id")
                    team_name, side = team_info.get(tid, ((team_block.get("team") or {}).get("name"), "unknown"))
                    appeared = []
                    for player_block in team_block.get("players") or []:
                        player = player_block.get("player") or {}
                        stats_list = player_block.get("statistics") or []
                        minutes = None
                        if stats_list:
                            games = stats_list[0].get("games") or {}
                            minutes = games.get("minutes")
                        name = player.get("name")
                        pid = player.get("id")
                        if name:
                            pkey = (str(tid), str(pid) if pid is not None else name)
                            detail = fixture_player_rows.get(pkey)
                            if detail is None:
                                detail = {
                                    "fixture_id": fid, "league": league_name, "season_start_year": season,
                                    "kickoff_utc": fixture.get("date"), "home_team": home.get("name"),
                                    "away_team": away.get("name"), "team_id": tid, "team_name": team_name,
                                    "team_side": side, "player_id": pid, "player_name": name,
                                    "shirt_number": player.get("number"), "position": player.get("pos"),
                                    "lineup_role": "not_listed_in_lineup", "is_starting": None,
                                    "is_listed_substitute": None, "minutes_played": None, "appeared": None,
                                    "lineup_source": "API-Football fixtures/lineups",
                                    "player_stats_source": "API-Football fixtures/players",
                                    "retrieved_at_utc": row["retrieved_at_utc"]
                                }
                                fixture_player_rows[pkey] = detail
                            detail["minutes_played"] = minutes
                            detail["appeared"] = (minutes > 0) if minutes is not None else None
                        if minutes is not None and minutes > 0 and name:
                            appeared.append({"name": name, "minutes": minutes, "number": player.get("number"), "position": player.get("pos")})
                    if tid == home.get("id"):
                        row["home_players_appeared"] = appeared
                    elif tid == away.get("id"):
                        row["away_players_appeared"] = appeared
                    else:
                        errors.append({"fixture_id": fid, "player_stats_error": f"Unexpected team ID in player stats response: {tid}"})
            except Exception as exc:
                errors.append({"fixture_id": fid, "player_stats_error": str(exc)})
            # Preserve actual substitution events independently from the bench list and player minutes.
            try:
                events_file = CACHE / f"fixture_{fid}_events.json"
                events_data = cached_json(events_file, lambda fid=fid: api("fixtures/events", {"fixture": fid}))
                row["substitution_events_status"] = "ok"
                for event in events_data.get("response", []):
                    if str(event.get("type", "")).strip().lower() not in {"subst", "substitution"}:
                        continue
                    event_team = event.get("team") or {}
                    tid = event_team.get("id")
                    team_name, side = team_info.get(tid, (event_team.get("name"), "unknown"))
                    clock = event.get("time") or {}
                    outgoing = event.get("player") or {}
                    incoming = event.get("assist") or {}
                    substitution_rows.append({
                        "fixture_id": fid, "league": league_name, "season_start_year": season,
                        "kickoff_utc": fixture.get("date"), "home_team": home.get("name"),
                        "away_team": away.get("name"), "team_id": tid, "team_name": team_name,
                        "team_side": side, "elapsed_minute": clock.get("elapsed"),
                        "extra_minute": clock.get("extra"), "outgoing_player_id": outgoing.get("id"),
                        "outgoing_player_name": outgoing.get("name"), "incoming_player_id": incoming.get("id"),
                        "incoming_player_name": incoming.get("name"), "event_detail": event.get("detail"),
                        "event_comments": event.get("comments"), "source": "API-Football fixtures/events",
                        "retrieved_at_utc": row["retrieved_at_utc"]
                    })
                    # If provider event identifies an incoming player absent from the lineup/stats lists,
                    # retain an explicit identity row rather than silently losing the substitution link.
                    if incoming.get("name"):
                        pkey = (str(tid), str(incoming.get("id")) if incoming.get("id") is not None else incoming.get("name"))
                        if pkey not in fixture_player_rows:
                            fixture_player_rows[pkey] = {
                                "fixture_id": fid, "league": league_name, "season_start_year": season,
                                "kickoff_utc": fixture.get("date"), "home_team": home.get("name"),
                                "away_team": away.get("name"), "team_id": tid, "team_name": team_name,
                                "team_side": side, "player_id": incoming.get("id"), "player_name": incoming.get("name"),
                                "shirt_number": None, "position": None, "lineup_role": "substitution_event_only",
                                "is_starting": None, "is_listed_substitute": None, "minutes_played": None,
                                "appeared": None, "lineup_source": "API-Football fixtures/events",
                                "player_stats_source": "API-Football fixtures/players",
                                "retrieved_at_utc": row["retrieved_at_utc"]
                            }
            except Exception as exc:
                row["substitution_events_status"] = "fetch_error"
                errors.append({"fixture_id": fid, "substitution_events_error": str(exc)})
            player_rows.extend(fixture_player_rows.values())
        rows.append(row)
        if n % 25 == 0:
            print(f"{league_name} {season}: processed {n}/{len(fixtures)} fixtures", flush=True)
        # Conservative pacing; API plan quotas may be stricter than this.
        time.sleep(float(os.getenv("REQUEST_PAUSE_SECONDS", "0.35")))

    json_path = OUT / f"{stem}.json"
    csv_path = OUT / f"{stem}.csv"
    players_csv_path = OUT / f"{stem}_players.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    columns = [k for k in rows[0].keys()]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    player_columns = [
        "fixture_id","league","season_start_year","kickoff_utc","home_team","away_team",
        "team_id","team_name","team_side","player_id","player_name","shirt_number","position",
        "lineup_role","is_starting","is_listed_substitute","minutes_played","appeared",
        "lineup_source","player_stats_source","retrieved_at_utc"
    ]
    with players_csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=player_columns)
        writer.writeheader()
        writer.writerows(player_rows)
    substitutions_csv_path = OUT / f"{stem}_substitutions.csv"
    substitution_columns = [
        "fixture_id","league","season_start_year","kickoff_utc","home_team","away_team",
        "team_id","team_name","team_side","elapsed_minute","extra_minute",
        "outgoing_player_id","outgoing_player_name","incoming_player_id","incoming_player_name",
        "event_detail","event_comments","source","retrieved_at_utc"
    ]
    with substitutions_csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=substitution_columns)
        writer.writeheader()
        writer.writerows(substitution_rows)
    player_keys = [(p["fixture_id"], p["team_id"], p["player_id"] or p["player_name"]) for p in player_rows]
    duplicate_player_keys = len(player_keys) - len(set(player_keys))
    normalized_name_keys = [(p["fixture_id"], p["team_id"], " ".join(str(p["player_name"] or "").casefold().split())) for p in player_rows]
    duplicate_fixture_team_player_names = len(normalized_name_keys) - len(set(normalized_name_keys))
    player_groups = {}
    for p in player_rows:
        player_groups.setdefault((p["fixture_id"], p["team_side"]), []).append(p)
    xi_complete_fixtures = set()
    for (fixture_id, side), group in player_groups.items():
        starters = sum(p["is_starting"] is True for p in group)
        if side in {"home", "away"} and starters == 11:
            xi_complete_fixtures.add(fixture_id)
    finished = [r for r in rows if r["status"] in {"FT", "AET", "PEN"}]
    report = {
        "provider": "API-Football", "league": league_name, "league_key": league_key,
        "season_start_year": season, "fixture_count": len(rows), "completed_count": len(finished),
        "completed_with_score": sum(r["result_90m"] is not None for r in finished),
        "completed_with_both_starting_xi": sum(len(r["home_starting_xi"] or []) == 11 and len(r["away_starting_xi"] or []) == 11 for r in finished),
        "normalized_player_rows": len(player_rows),
        "substitution_events_rows": len(substitution_rows),
        "fixtures_with_substitution_events_data": len({r["fixture_id"] for r in substitution_rows}),
        "normalized_substitutions_csv": str(substitutions_csv_path),
        "unique_fixture_team_player_keys": len(set(player_keys)),
        "duplicate_fixture_team_player_keys": duplicate_player_keys,
        "duplicate_fixture_team_normalized_player_names": duplicate_fixture_team_player_names,
        "fixtures_with_both_teams_exactly_11_starting_xi": sum(sum(1 for p in player_rows if p["fixture_id"] == fid and p["team_side"] == "home" and p["is_starting"] is True) == 11 and sum(1 for p in player_rows if p["fixture_id"] == fid and p["team_side"] == "away" and p["is_starting"] is True) == 11 for fid in {p["fixture_id"] for p in player_rows}),
        "normalized_player_csv": str(players_csv_path),
        "completed_with_both_teams_appearances": sum(r["home_players_appeared"] is not None and r["away_players_appeared"] is not None for r in finished),
        "completed_with_both_xg": sum(r["home_xg"] is not None and r["away_xg"] is not None for r in finished),
        "missing_xg_count": sum(r["xg_status"] != "ok" for r in finished),
        "fetch_errors": errors, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "Season parameter is the start year: 2025 means 2025-26; 2026 means 2026-27.",
            "Provider plan may limit historical fixture/lineup/statistics endpoints.",
            "Missing provider values remain null; they are not imputed.",
            "Only actual player minutes > 0 count as appearances; starting XI comes from the lineup endpoint.",
            "Normalized *_players.csv stores one row per fixture/team/player, separating lineup role from actual appearance. Listed substitutes are not counted as appearances without minutes > 0.",
            "Normalized *_substitutions.csv stores substitution events from fixtures/events; event coverage depends on provider response and must be audited separately.",
            "90-minute outcome uses the provider's full-time goals fields; AET/PEN fixtures may require separate regulation-time verification before use as a strict 90-minute result."
        ]
    }
    (OUT / f"{stem}_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        print(f"Completed with {len(errors)} fixture-level errors; inspect {OUT / (stem + '_audit.json')}")
    if not finished:
        raise SystemExit("No completed fixtures; refusing to report season as populated.")

if __name__ == "__main__":
    main()
