#!/usr/bin/env python3
"""Snapshot the official China Sports Lottery (竞彩) handicap odds.

The official endpoint only exposes matches currently on sale, and its historical result
endpoints are dead (403 on the jc gateway, empty payload on the uniform one), so the
closing handicap price has to be captured before kickoff rather than fetched later.

Design:
  * every run stores one snapshot per on-sale match, keyed by (match_id, snapshot_at),
    which makes repeated runs idempotent
  * a closing table is rebuilt from the snapshots: for each match, the last snapshot taken
    before kickoff
  * results are not available from this endpoint; a separate column set is reserved so a
    results job can attach scores later (the jc-daily-report site carries scores plus the
    same 竞彩 number, so that is the intended join)

The handicap pool (hhad) is what the product needs: goal line plus win/draw/loss odds.

Usage:
    python data_pipeline/collect_sporttery_odds.py            # one snapshot + closing refresh
    python data_pipeline/collect_sporttery_odds.py --once --quiet
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
POOLS = "had,hhad,crs,ttg,hafu"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 Mobile/15E148",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sporttery_snapshot (
  match_id      TEXT NOT NULL,
  snapshot_at   TEXT NOT NULL,
  match_num     TEXT,
  business_date TEXT,
  league        TEXT,
  home          TEXT,
  away          TEXT,
  kickoff_utc   TEXT,
  match_status  TEXT,
  sell_status   TEXT,
  had_h REAL, had_d REAL, had_a REAL,
  hhad_line REAL, hhad_h REAL, hhad_d REAL, hhad_a REAL,
  hhad_update   TEXT,
  PRIMARY KEY (match_id, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_sporttery_snapshot_kickoff
  ON sporttery_snapshot(match_id, kickoff_utc);

CREATE TABLE IF NOT EXISTS sporttery_closing (
  match_id        TEXT PRIMARY KEY,
  match_num       TEXT,
  business_date   TEXT,
  league          TEXT,
  home            TEXT,
  away            TEXT,
  kickoff_utc     TEXT,
  had_h REAL, had_d REAL, had_a REAL,
  hhad_line REAL, hhad_h REAL, hhad_d REAL, hhad_a REAL,
  last_snapshot_at TEXT,
  snapshots       INTEGER
);

CREATE TABLE IF NOT EXISTS sporttery_result (
  match_id    TEXT PRIMARY KEY,
  home_goals  INTEGER,
  away_goals  INTEGER,
  source      TEXT,
  updated_at  TEXT
);
"""


def log(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message, flush=True)


def fetch() -> dict:
    url = API + "?" + urllib.parse.urlencode({"poolCode": POOLS, "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers=HEADERS)
    with opener.open(request, timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if not payload.get("success", True):
        raise RuntimeError(f"sporttery error: {payload.get('errorMessage')}")
    return payload


def to_float(value) -> float | None:
    try:
        number = float(value)
        return number if number > 1.0 else None
    except (TypeError, ValueError):
        return None


def parse_matches(payload: dict) -> list[dict]:
    value = payload.get("value") or {}
    rows: list[dict] = []
    for group in value.get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            match_date = (item.get("matchDate") or "").strip()
            match_time = (item.get("matchTime") or "00:00:00").strip()
            kickoff = None
            if match_date:
                try:
                    local = datetime.fromisoformat(f"{match_date}T{match_time}")
                    kickoff = (local - timedelta(hours=8)).replace(
                        tzinfo=timezone.utc).isoformat(timespec="seconds")
                except ValueError:
                    kickoff = None
            had = item.get("had") or {}
            hhad = item.get("hhad") or {}
            try:
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (TypeError, ValueError):
                line = None
            rows.append({
                "match_id": str(item.get("matchId") or ""),
                "match_num": item.get("matchNumStr") or "",
                "business_date": item.get("businessDate") or group.get("businessDate") or "",
                "league": item.get("leagueAbbName") or item.get("leagueAllName") or "",
                "home": item.get("homeTeamAbbName") or item.get("homeTeamAllName") or "",
                "away": item.get("awayTeamAbbName") or item.get("awayTeamAllName") or "",
                "kickoff_utc": kickoff,
                "match_status": item.get("matchStatus") or "",
                "sell_status": str(item.get("sellStatus") or ""),
                "had_h": to_float(had.get("h")), "had_d": to_float(had.get("d")),
                "had_a": to_float(had.get("a")),
                "hhad_line": line, "hhad_h": to_float(hhad.get("h")),
                "hhad_d": to_float(hhad.get("d")), "hhad_a": to_float(hhad.get("a")),
                "hhad_update": f"{hhad.get('updateDate', '')} {hhad.get('updateTime', '')}".strip(),
            })
    return [r for r in rows if r["match_id"]]


def store(connection: sqlite3.Connection, rows: list[dict], snapshot_at: str) -> int:
    inserted = 0
    for row in rows:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO sporttery_snapshot
               (match_id, snapshot_at, match_num, business_date, league, home, away,
                kickoff_utc, match_status, sell_status, had_h, had_d, had_a,
                hhad_line, hhad_h, hhad_d, hhad_a, hhad_update)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["match_id"], snapshot_at, row["match_num"], row["business_date"],
             row["league"], row["home"], row["away"], row["kickoff_utc"],
             row["match_status"], row["sell_status"], row["had_h"], row["had_d"],
             row["had_a"], row["hhad_line"], row["hhad_h"], row["hhad_d"],
             row["hhad_a"], row["hhad_update"]))
        inserted += cursor.rowcount
    connection.commit()
    return inserted


def refresh_closing(connection: sqlite3.Connection) -> int:
    """Rebuild the closing table: last snapshot strictly before kickoff."""
    connection.execute("DELETE FROM sporttery_closing")
    connection.execute("""
        INSERT INTO sporttery_closing
        SELECT s.match_id, s.match_num, s.business_date, s.league, s.home, s.away,
               s.kickoff_utc, s.had_h, s.had_d, s.had_a,
               s.hhad_line, s.hhad_h, s.hhad_d, s.hhad_a,
               s.snapshot_at,
               (SELECT COUNT(*) FROM sporttery_snapshot x WHERE x.match_id = s.match_id)
        FROM sporttery_snapshot s
        JOIN (
            SELECT match_id, MAX(snapshot_at) AS latest
            FROM sporttery_snapshot
            WHERE kickoff_utc IS NOT NULL AND snapshot_at < kickoff_utc
            GROUP BY match_id
        ) pick ON pick.match_id = s.match_id AND pick.latest = s.snapshot_at
    """)
    connection.commit()
    return connection.execute("SELECT COUNT(*) FROM sporttery_closing").fetchone()[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="take a single snapshot and exit")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--closing-only", action="store_true",
                        help="rebuild the closing table without fetching")
    args = parser.parse_args()

    connection = sqlite3.connect(DB)
    connection.executescript(SCHEMA)
    snapshot_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    inserted = 0
    total = 0
    last = connection.execute(
        "SELECT MAX(snapshot_at) FROM sporttery_snapshot").fetchone()[0]
    too_soon = False
    if last:
        try:
            gap = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(last)).total_seconds()
            too_soon = gap < 60
        except ValueError:
            too_soon = False
    if not args.closing_only and too_soon:
        log(f"skipped: last snapshot {last} was under 60s ago", args.quiet)

    if not args.closing_only and not too_soon:
        payload = fetch()
        rows = parse_matches(payload)
        total = len(rows)
        inserted = store(connection, rows, snapshot_at)
        log(f"snapshot {snapshot_at}: {total} on sale, {inserted} new rows", args.quiet)

    closing = refresh_closing(connection)
    snapshots = connection.execute("SELECT COUNT(*) FROM sporttery_snapshot").fetchone()[0]
    with_line = connection.execute(
        "SELECT COUNT(*) FROM sporttery_closing WHERE hhad_line IS NOT NULL").fetchone()[0]
    log(f"closing rows: {closing} ({with_line} with a handicap line); "
        f"snapshots stored: {snapshots}", args.quiet)

    if args.once:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
