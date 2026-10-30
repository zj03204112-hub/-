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
    errors = []
    for n, item in enumerate(fixtures, 1):
        fixture = item.get("fixture", {})
        status = (fixture.get("status") or {}).get("short")
        goals = item.get("goals") or {}
        teams = item.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        # Keep all scheduled fixtures in the audit; actual score/result only for completed matches.
        completed = status in {"FT", "AET", "PEN"}
        hg, ag = (goals.get("home"), goals.get("away")) if completed else (None, None)
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
        }
        if completed:
            fid = fixture.get("id")
            try:
                lineup_file = CACHE / f"fixture_{fid}_lineups.json"
                lineup_data = cached_json(lineup_file, lambda fid=fid: api("fixtures/lineups", {"fixture": fid}))
                lineup_response = lineup_data.get("response", [])
                for lineup in lineup_response:
                    team = (lineup.get("team") or {}).get("id")
                    start = lineup.get("startXI") or []
                    subs = lineup.get("substitutes") or []
                    starters = [p.get("player", {}).get("name") for p in start if p.get("player", {}).get("name")]
                    # API-Football lineup substitutes are listed, not proof of appearance.
                    row["home_starting_xi" if team == home.get("id") else "away_starting_xi"] = starters
                    # Actual appearances will be resolved from player fixture statistics below.
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
                row["lineup_status"] = "ok" if row["home_starting_xi"] is not None and row["away_starting_xi"] is not None else "missing_lineup"
                row["xg_status"] = "ok" if row["home_xg"] is not None and row["away_xg"] is not None else "missing_xg"
            except Exception as exc:
                errors.append({"fixture_id": fid, "error": str(exc)})
                row["lineup_status"] = "fetch_error"
                row["xg_status"] = "fetch_error"
            # API-Football player statistics are a separate request and required to identify subs who actually entered.
            try:
                player_file = CACHE / f"fixture_{fid}_players.json"
                player_data = cached_json(player_file, lambda fid=fid: api("fixtures/players", {"fixture": fid}))
                for team_block in player_data.get("response", []):
                    tid = (team_block.get("team") or {}).get("id")
                    appeared = []
                    for player_block in team_block.get("players") or []:
                        player = player_block.get("player") or {}
                        stats_list = player_block.get("statistics") or []
                        minutes = None
                        if stats_list:
                            games = stats_list[0].get("games") or {}
                            minutes = games.get("minutes")
                        if minutes is not None and minutes > 0 and player.get("name"):
                            appeared.append({"name": player["name"], "minutes": minutes, "number": player.get("number"), "position": player.get("pos")})
                    row["home_players_appeared" if tid == home.get("id") else "away_players_appeared"] = appeared
            except Exception as exc:
                errors.append({"fixture_id": fid, "player_stats_error": str(exc)})
        rows.append(row)
        if n % 25 == 0:
            print(f"{league_name} {season}: processed {n}/{len(fixtures)} fixtures", flush=True)
        # Conservative pacing; API plan quotas may be stricter than this.
        time.sleep(float(os.getenv("REQUEST_PAUSE_SECONDS", "0.35")))

    json_path = OUT / f"{stem}.json"
    csv_path = OUT / f"{stem}.csv"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    columns = [k for k in rows[0].keys()]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    finished = [r for r in rows if r["status"] in {"FT", "AET", "PEN"}]
    report = {
        "provider": "API-Football", "league": league_name, "league_key": league_key,
        "season_start_year": season, "fixture_count": len(rows), "completed_count": len(finished),
        "completed_with_score": sum(r["result_90m"] is not None for r in finished),
        "completed_with_both_starting_xi": sum(r["home_starting_xi"] is not None and r["away_starting_xi"] is not None for r in finished),
        "completed_with_both_teams_appearances": sum(r["home_players_appeared"] is not None and r["away_players_appeared"] is not None for r in finished),
        "completed_with_both_xg": sum(r["home_xg"] is not None and r["away_xg"] is not None for r in finished),
        "missing_xg_count": sum(r["xg_status"] != "ok" for r in finished),
        "fetch_errors": errors, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "Season parameter is the start year: 2025 means 2025-26; 2026 means 2026-27.",
            "Provider plan may limit historical fixture/lineup/statistics endpoints.",
            "Missing provider values remain null; they are not imputed.",
            "Only actual player minutes > 0 count as appearances; starting XI comes from the lineup endpoint.",
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
