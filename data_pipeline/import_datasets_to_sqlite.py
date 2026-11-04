#!/usr/bin/env python3
"""Load the per-league CSV detail layers into the SQLite analysis database.

The repository already had a ``matches`` / ``results`` / ``player_match_stats`` model
fed by the older collectors, which stopped in September. The new detail layer lives in
CSV + batch JSON files per league. This script imports that layer into the same
database under a separate ``dsl_*`` namespace so the old tables keep working while the
new season becomes queryable.

Design notes
------------
* one row per match in ``dsl_matches`` with a stable ``match_key`` of
  ``league:season:date:home:away``
* every other table carries the same ``match_key`` so joins are trivial
* imports are idempotent: the rows of a league are deleted before they are reinserted
* the source file path is kept on the matches table so any row can be traced back
* missing pieces (for example J1 has no shot table) are simply skipped, never invented

Usage:
    python data_pipeline/import_datasets_to_sqlite.py --dry-run
    python data_pipeline/import_datasets_to_sqlite.py
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
DB_PATH = os.path.join(REPO_ROOT, "football_model_database.sqlite")
REPORT = os.path.join(SEASON_DIR, "dataset_sqlite_import_report_"
                                  f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.json")

# league_code, file prefix, season label
LEAGUES = [
    ("LALIGA", "LaLiga_2026_27", "2026-27"),
    ("EPL", "EPL_2026_27", "2026-27"),
    ("BUNDESLIGA", "BUNDESLIGA_2026_27", "2026-27"),
    ("SERIEA", "SERIEA_2026_27", "2026-27"),
    ("UCL", "UCL_2026_27", "2026-27"),
    ("KLEAGUE1", "KLEAGUE1_2026_27", "2026"),
    ("LIGUE1", "LIGUE1_2026_27", "2026-27"),
    ("EREDIVISIE", "EREDIVISIE_2026_27", "2026-27"),
    ("ALEAGUE", "ALEAGUE_2026_27", "2026/27"),
    ("J1", "J1_League_2026_27", "2026/27"),
]

DDL = """
CREATE TABLE IF NOT EXISTS dsl_matches (
    match_key TEXT PRIMARY KEY,
    league_code TEXT NOT NULL,
    season TEXT,
    match_date TEXT,
    home_team TEXT,
    away_team TEXT,
    score TEXT,
    matchday TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dsl_players (
    match_key TEXT NOT NULL,
    team TEXT,
    player TEXT,
    is_starting INTEGER,
    appearance_status TEXT,
    entry_minute TEXT,
    exit_minute TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dsl_substitutions (
    match_key TEXT NOT NULL,
    team TEXT,
    player_in TEXT,
    player_out TEXT,
    minute TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dsl_events (
    match_key TEXT NOT NULL,
    minute TEXT,
    clock TEXT,
    period TEXT,
    team TEXT,
    person TEXT,
    person_role TEXT,
    event_type TEXT,
    event_detail TEXT,
    assist TEXT,
    assist_source TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dsl_match_meta (
    match_key TEXT PRIMARY KEY,
    formation_home TEXT,
    formation_away TEXT,
    manager_home TEXT,
    manager_away TEXT,
    venue TEXT,
    referee TEXT,
    attendance TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dsl_shots (
    match_key TEXT NOT NULL,
    minute TEXT,
    added_minute TEXT,
    team TEXT,
    player TEXT,
    x REAL,
    y REAL,
    xg REAL,
    xgot REAL,
    shot_event TEXT,
    shot_type TEXT,
    situation TEXT,
    is_blocked TEXT,
    is_on_target TEXT,
    is_own_goal TEXT,
    is_from_inside_box TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dsl_assists (
    match_key TEXT NOT NULL,
    minute TEXT,
    team TEXT,
    scorer TEXT,
    assist TEXT,
    status TEXT,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_dsl_players_key ON dsl_players(match_key);
CREATE INDEX IF NOT EXISTS idx_dsl_events_key ON dsl_events(match_key);
CREATE INDEX IF NOT EXISTS idx_dsl_shots_key ON dsl_shots(match_key);
CREATE INDEX IF NOT EXISTS idx_dsl_subs_key ON dsl_substitutions(match_key);
CREATE INDEX IF NOT EXISTS idx_dsl_matches_league ON dsl_matches(league_code, match_date);
"""


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_scores(prefix: str) -> dict[tuple, str]:
    """(date, home, away) -> 'x-y' from the per-match batch JSON files."""
    scores: dict[tuple, str] = {}
    for path in glob.glob(os.path.join(SEASON_DIR, f"{prefix}*player_detail_batch*.json")):
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if "date" in payload and "home" in payload:          # one file per match
            scores[(payload["date"], payload["home"], payload["away"])] = payload.get("score", "")
        elif isinstance(payload.get("matches"), list):        # aggregated batch (J1)
            for item in payload["matches"]:
                if item.get("date") and item.get("home"):
                    scores[(item["date"], item["home"], item["away"])] = (
                        item.get("score") or f'{item.get("home_goals", "")}-{item.get("away_goals", "")}')
    return scores


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    connection = sqlite3.connect(DB_PATH)
    connection.executescript(DDL)
    summary = []

    for league_code, prefix, season in LEAGUES:
        players_path = os.path.join(SEASON_DIR, f"{prefix}_players.csv")
        players = read_csv(players_path)
        if not players:
            summary.append({"league": league_code, "status": "no player csv"})
            continue

        subs = read_csv(os.path.join(SEASON_DIR, f"{prefix}_substitutions.csv"))
        events = read_csv(os.path.join(SEASON_DIR, f"{prefix}_events.csv"))
        meta = read_csv(os.path.join(SEASON_DIR, f"{prefix}_match_meta.csv"))
        shots = read_csv(os.path.join(SEASON_DIR, f"{prefix}_shots.csv"))
        assists = read_csv(os.path.join(SEASON_DIR, f"{prefix}_assists.csv"))
        scores = load_scores(prefix)

        match_keys = {}
        for row in players:
            key = (row["Date"], row["HomeTeam"], row["AwayTeam"])
            match_keys.setdefault(key, f"{league_code}:{season}:{row['Date']}:{row['HomeTeam']}:{row['AwayTeam']}")

        if args.dry_run:
            summary.append({"league": league_code, "fixtures": len(match_keys),
                            "players": len(players), "subs": len(subs), "events": len(events),
                            "shots": len(shots)})
            continue

        relative_source = os.path.relpath(players_path, REPO_ROOT).replace(os.sep, "/")
        connection.execute("DELETE FROM dsl_players WHERE match_key IN (SELECT match_key FROM dsl_matches WHERE league_code=?)", (league_code,))
        connection.execute("DELETE FROM dsl_substitutions WHERE match_key IN (SELECT match_key FROM dsl_matches WHERE league_code=?)", (league_code,))
        connection.execute("DELETE FROM dsl_events WHERE match_key IN (SELECT match_key FROM dsl_matches WHERE league_code=?)", (league_code,))
        connection.execute("DELETE FROM dsl_shots WHERE match_key IN (SELECT match_key FROM dsl_matches WHERE league_code=?)", (league_code,))
        connection.execute("DELETE FROM dsl_assists WHERE match_key IN (SELECT match_key FROM dsl_matches WHERE league_code=?)", (league_code,))
        connection.execute("DELETE FROM dsl_match_meta WHERE match_key IN (SELECT match_key FROM dsl_matches WHERE league_code=?)", (league_code,))
        connection.execute("DELETE FROM dsl_matches WHERE league_code=?", (league_code,))

        connection.executemany(
            "INSERT OR REPLACE INTO dsl_matches (match_key, league_code, season, match_date, home_team,"
            " away_team, score, matchday, source) VALUES (?,?,?,?,?,?,?,?,?)",
            [(key, league_code, season, date, home, away,
              scores.get((date, home, away), ""),
              (next((m.get("Matchday") or "" for m in meta
                     if (m.get("Date"), m.get("HomeTeam"), m.get("AwayTeam")) == (date, home, away)), "")),
              relative_source)
             for (date, home, away), key in match_keys.items()])

        connection.executemany(
            "INSERT INTO dsl_players (match_key, team, player, is_starting, appearance_status,"
            " entry_minute, exit_minute, source) VALUES (?,?,?,?,?,?,?,?)",
            [(match_keys[(r["Date"], r["HomeTeam"], r["AwayTeam"])], r["Team"], r["Player"],
              1 if r["IsStarting"] == "true" else 0, r["AppearanceStatus"],
              r["EntryMinute"], r["ExitMinute"], r["Source"]) for r in players])

        connection.executemany(
            "INSERT INTO dsl_substitutions (match_key, team, player_in, player_out, minute, source)"
            " VALUES (?,?,?,?,?,?)",
            [(match_keys[(r["Date"], r["HomeTeam"], r["AwayTeam"])], r["Team"], r["PlayerIn"],
              r["PlayerOut"], r["Minute"], r["Source"]) for r in subs])

        connection.executemany(
            "INSERT INTO dsl_events (match_key, minute, clock, period, team, person, person_role,"
            " event_type, event_detail, assist, assist_source, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(match_keys[(r["Date"], r["HomeTeam"], r["AwayTeam"])], r["Minute"], r.get("Clock", ""),
              r.get("Period", ""), r["Team"], r["Person"], r.get("PersonRole", ""), r["EventType"],
              r.get("EventDetail", ""), r.get("Assist", ""), r.get("AssistSource", ""), r["Source"])
             for r in events])

        connection.executemany(
            "INSERT OR REPLACE INTO dsl_match_meta (match_key, formation_home, formation_away,"
            " manager_home, manager_away, venue, referee, attendance, source)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(match_keys[(r["Date"], r["HomeTeam"], r["AwayTeam"])], r.get("FormationHome", ""),
              r.get("FormationAway", ""), r.get("ManagerHome", ""), r.get("ManagerAway", ""),
              r.get("Venue", ""), r.get("Referee", ""), r.get("Attendance", ""), r.get("Source", ""))
             for r in meta])

        connection.executemany(
            "INSERT INTO dsl_shots (match_key, minute, added_minute, team, player, x, y, xg, xgot,"
            " shot_event, shot_type, situation, is_blocked, is_on_target, is_own_goal,"
            " is_from_inside_box, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(match_keys[(r["Date"], r["HomeTeam"], r["AwayTeam"])], r.get("Minute", ""),
              r.get("AddedMinute", ""), r.get("Team", ""), r.get("Player", ""), to_float(r.get("X")),
              to_float(r.get("Y")), to_float(r.get("XG")), to_float(r.get("XGOT")),
              r.get("EventType", ""), r.get("ShotType", ""), r.get("Situation", ""),
              r.get("IsBlocked", ""), r.get("IsOnTarget", ""), r.get("IsOwnGoal", ""),
              r.get("IsFromInsideBox", ""), r.get("Source", "")) for r in shots])

        connection.executemany(
            "INSERT INTO dsl_assists (match_key, minute, team, scorer, assist, status, source)"
            " VALUES (?,?,?,?,?,?,?)",
            [(match_keys[(r["Date"], r["HomeTeam"], r["AwayTeam"])], r.get("Minute", ""),
              r.get("Team", ""), r.get("Scorer", ""), r.get("Assist", ""), r.get("Status", ""),
              r.get("Source", "")) for r in assists])

        connection.commit()
        summary.append({"league": league_code, "fixtures": len(match_keys),
                        "players": len(players), "subs": len(subs), "events": len(events),
                        "shots": len(shots), "assists": len(assists), "meta": len(meta)})

    counts = {}
    if not args.dry_run:
        for table in ("dsl_matches", "dsl_players", "dsl_substitutions", "dsl_events",
                      "dsl_match_meta", "dsl_shots", "dsl_assists"):
            counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        report = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "database": os.path.basename(DB_PATH),
            "namespace": "dsl_*",
            "leagues": summary,
            "table_counts": counts,
            "notes": [
                "The legacy matches/results/player_match_stats tables are untouched.",
                "match_key is league:season:date:home:away and is the join key everywhere.",
                "Source columns carry the original provider so nothing loses provenance.",
            ],
        }
        json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(REPORT, "a", encoding="utf-8").write("\n")
    connection.close()

    for entry in summary:
        print(" ", entry)
    if counts:
        print("table counts:", counts)
        print("report:", os.path.relpath(REPORT, REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
