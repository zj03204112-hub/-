#!/usr/bin/env python3
"""Daily injury and suspension snapshot from FotMob.

Every team page carries the list of players who are unavailable for the next match:

  overview.lastLineupStats.unavailable[]
      name, shirtNumber, positionId, marketValue, performance.seasonRating
      unavailability: { injuryId, type (injury|suspension), expectedReturn }

That is exactly what a pre-match team-strength adjustment needs, and the marketValue field
means the loss can be weighted by how important the missing player is.

The endpoint only reports the *current* state, there is no historical view, so the data has to
be accumulated: one snapshot per day, keyed by (team_id, snapshot_date, player_id) so a run
cannot duplicate a day.

To keep the daily download sane (each team page is about 0.9 MB) only teams that play inside
the next few days are fetched by default; --all-teams overrides that.

Usage:
    python data_pipeline/collect_injuries_fotmob.py
    python data_pipeline/collect_injuries_fotmob.py --days-ahead 10 --all-teams
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
API = "https://www.fotmob.com/api/data/teams?id={team_id}"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "Chrome/140 Safari/537.36",
           "Accept": "application/json", "Referer": "https://www.fotmob.com/",
           "Accept-Encoding": "gzip"}

FIXTURE_FILES = [
    "EPL_2026_27_fixtures.json", "BUNDESLIGA_2026_27_fixtures.json",
    "SERIEA_2026_27_fixtures.json", "LIGUE1_2026_27_fixtures.json",
    "EREDIVISIE_2026_27_fixtures.json", "LaLiga_2026_27_fixtures.json",
    "KLEAGUE1_2026_27_fixtures.json", "ALEAGUE_2026_27_fixtures.json",
    "J1_League_2026_27_fixtures_fotmob.json",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS injury_snapshot (
  team_id        TEXT NOT NULL,
  team_name      TEXT,
  snapshot_date  TEXT NOT NULL,
  player_id      TEXT NOT NULL,
  player_name    TEXT,
  shirt_number   TEXT,
  position_id    INTEGER,
  market_value   REAL,
  season_rating  REAL,
  absence_type   TEXT,
  injury_id      INTEGER,
  expected_return TEXT,
  PRIMARY KEY (team_id, snapshot_date, player_id)
);
CREATE INDEX IF NOT EXISTS idx_injury_date ON injury_snapshot(snapshot_date);

CREATE TABLE IF NOT EXISTS injury_team_day (
  team_id       TEXT NOT NULL,
  team_name     TEXT,
  snapshot_date TEXT NOT NULL,
  squad_size    INTEGER,
  out_count     INTEGER,
  injury_count  INTEGER,
  suspend_count INTEGER,
  out_value     REAL,
  squad_value   REAL,
  PRIMARY KEY (team_id, snapshot_date)
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def load_teams(days_ahead: int, all_teams: bool) -> dict[str, str]:
    """team_id -> team_name, limited to teams with a fixture inside the window."""
    today = date(2026, 11, 5)
    horizon = today + timedelta(days=days_ahead)
    teams: dict[str, str] = {}
    for name in FIXTURE_FILES:
        path = os.path.join(SEASON_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            fixtures = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for fixture in fixtures:
            status = fixture.get("status") or ""
            day = fixture.get("date") or ""
            if not all_teams:
                if status in ("FT", "AET", "Pen"):
                    continue
                try:
                    fixture_date = date.fromisoformat(day)
                except ValueError:
                    continue
                if not (today - timedelta(days=1) <= fixture_date <= horizon):
                    continue
            for side in ("home", "away"):
                team_id = fixture.get(f"{side}Id")
                team_name = fixture.get(side)
                if team_id and team_name:
                    teams[str(team_id)] = team_name
    return teams


def fetch_team(team_id: str, retries: int = 3) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(API.format(team_id=team_id), headers=HEADERS)
            with opener.open(request, timeout=40) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-ahead", type=int, default=7)
    parser.add_argument("--all-teams", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    import sqlite3
    connection = sqlite3.connect(DB)
    connection.executescript(SCHEMA)
    snapshot_date = datetime.now(timezone.utc).date().isoformat()

    teams = load_teams(args.days_ahead, args.all_teams)
    if args.limit:
        teams = dict(list(teams.items())[:args.limit])
    log(f"teams to fetch: {len(teams)} (window {args.days_ahead} days"
        f"{', all teams' if args.all_teams else ''})")

    players = 0
    teams_done = 0
    failed = 0
    for index, (team_id, team_name) in enumerate(sorted(teams.items()), start=1):
        detail = fetch_team(team_id)
        if not detail:
            failed += 1
            continue
        overview = detail.get("overview") or {}
        lineup = overview.get("lastLineupStats") or {}
        unavailable = lineup.get("unavailable") or []
        squad = overview.get("squad") or []
        squad_value = 0.0
        squad_size = 0
        for group in squad:
            for member in group.get("members") or []:
                if (member.get("role") or {}).get("key") == "coach":
                    continue
                squad_size += 1
                try:
                    squad_value += float(member.get("transferValue") or 0)
                except (TypeError, ValueError):
                    pass

        out_value = 0.0
        injuries = suspensions = 0
        for player in unavailable:
            info = player.get("unavailability") or {}
            absence_type = info.get("type") or ""
            injuries += int(absence_type == "injury")
            suspensions += int(absence_type == "suspension")
            try:
                value = float(player.get("marketValue") or 0)
            except (TypeError, ValueError):
                value = 0.0
            out_value += value
            rating = ((player.get("performance") or {}).get("seasonRating"))
            connection.execute(
                """INSERT OR REPLACE INTO injury_snapshot
                   (team_id, team_name, snapshot_date, player_id, player_name, shirt_number,
                    position_id, market_value, season_rating, absence_type, injury_id,
                    expected_return)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (team_id, team_name, snapshot_date, str(player.get("id")),
                 player.get("name"), player.get("shirtNumber"), player.get("positionId"),
                 value, rating, absence_type, info.get("injuryId"),
                 info.get("expectedReturn")))
            players += 1

        connection.execute(
            """INSERT OR REPLACE INTO injury_team_day
               (team_id, team_name, snapshot_date, squad_size, out_count, injury_count,
                suspend_count, out_value, squad_value)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (team_id, team_name, snapshot_date, squad_size, len(unavailable), injuries,
             suspensions, out_value, squad_value))
        teams_done += 1
        if index % 20 == 0:
            log(f"  progress {index}/{len(teams)} (players stored {players})")
            connection.commit()
        time.sleep(0.35)

    connection.commit()
    total_players = connection.execute("SELECT COUNT(*) FROM injury_snapshot").fetchone()[0]
    total_days = connection.execute("SELECT COUNT(DISTINCT snapshot_date) FROM injury_snapshot").fetchone()[0]
    log(f"done: {teams_done} teams, {players} absence rows, {failed} failed")
    log(f"table totals: {total_players} absence rows over {total_days} snapshot day(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
