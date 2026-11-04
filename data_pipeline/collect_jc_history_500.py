#!/usr/bin/env python3
"""Backfill historical jingcai (竞彩) handicap data from 500.com static XML.

Two public static files exist per match date, no login and no rate limiting:

  .../jczq/xml/hisdata/{YYYY}/{MMDD}/match.xml   identity, score, line, results
  .../jczq/xml/hisdata/{YYYY}/{MMDD}/odds.xml    odds snapshots per bookmaker

match.xml carries what the product actually needs: the jingcai match number, both teams,
the final score, the handicap line (rangqiu), the plain 1X2 prices and the settled
handicap result. odds.xml adds the three-way handicap prices (h/d/a) under <rq>, plus Asian
handicap water, European prices and the over/under line.

Together they give a multi-season history of the exact product we model, which the official
sporttery API cannot provide (its historical endpoints are dead) and which would otherwise
require running a live snapshot job for months.

Usage:
    python data_pipeline/collect_jc_history_500.py --start 2026-01-01 --end 2026-11-04
    python data_pipeline/collect_jc_history_500.py --start 2022-01-01 --end 2026-11-04 --quiet
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
BASE = "https://www.500.com/static/public/jczq/xml/hisdata/{year}/{mmdd}/{name}.xml"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/120 Safari/537.36",
    "Referer": "https://trade.500.com/",
    "Accept": "application/xml,text/xml,*/*",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jc_history (
  match_id      TEXT PRIMARY KEY,
  match_date    TEXT,
  matchnum      TEXT,
  league        TEXT,
  home          TEXT,
  away          TEXT,
  home_score    INTEGER,
  away_score    INTEGER,
  handicap_line REAL,
  win           REAL,     -- plain 1X2 prices
  draw          REAL,
  lost          REAL,
  hhad_h        REAL,     -- three-way handicap prices
  hhad_d        REAL,
  hhad_a        REAL,
  hhad_source   TEXT,
  result_spf    TEXT,     -- plain result, and settled handicap result
  result_hhad   TEXT,
  asian_hand    TEXT,
  europe_avg    TEXT,
  overunder     TEXT,
  updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_jc_history_date ON jc_history(match_date);
"""


def log(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message, flush=True)


class RateLimited(Exception):
    """Raised when 500.com answers with its anti-bot challenge instead of XML."""


def fetch(url: str, retries: int = 3) -> bytes | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with opener.open(request, timeout=30) as response:
                body = response.read()
            # 500.com serves a small obfuscated JS challenge when it rate-limits an IP.
            if not body.lstrip().startswith(b"<?xml"):
                raise RateLimited(body[:60])
            return body
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            time.sleep(1.2 * attempt)
        except RateLimited:
            if attempt < retries:
                time.sleep(20 * attempt)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1.2 * attempt)
    return None


def numbers(text: str | None, count: int) -> list[str]:
    if not text:
        return []
    parts = [p.strip() for p in text.split(",")]
    return parts if len(parts) == count else []


def parse_day(day: date) -> list[dict]:
    year, mmdd = day.strftime("%Y"), day.strftime("%m%d")
    match_xml = fetch(BASE.format(year=year, mmdd=mmdd, name="match"))
    if not match_xml:
        return []
    odds_xml = fetch(BASE.format(year=year, mmdd=mmdd, name="odds"))

    rq_by_id: dict[str, list[str]] = {}
    asian_by_id: dict[str, str] = {}
    europe_by_id: dict[str, str] = {}
    dxq_by_id: dict[str, str] = {}
    if odds_xml:
        try:
            root = ET.fromstring(odds_xml)
        except ET.ParseError:
            root = None
        if root is not None:
            for node in root.iter("match"):
                key = node.get("id")
                rq = node.find("rq")
                if rq is not None:
                    for attribute in ("avg", "lb", "wd", "bw", "wl"):
                        values = numbers(rq.get(attribute), 3)
                        if values:
                            rq_by_id[key] = values
                            break
                asian = node.find("asian")
                if asian is not None and asian.get("am"):
                    asian_by_id[key] = asian.get("am")
                europe = node.find("europe")
                if europe is not None and europe.get("avg"):
                    europe_by_id[key] = europe.get("avg")
                dxq = node.find("dxq")
                if dxq is not None:
                    first = next(iter(dxq.attrib.values()), "")
                    dxq_by_id[key] = first

    try:
        root = ET.fromstring(match_xml)
    except ET.ParseError:
        return []

    rows: list[dict] = []
    for node in root.iter("match"):
        key = node.get("id")
        score = (node.get("score") or "").strip()
        home_score = away_score = None
        if ":" in score:
            try:
                home_score, away_score = (int(x) for x in score.split(":", 1))
            except ValueError:
                home_score = away_score = None

        def float_or_none(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        rq = rq_by_id.get(key) or []
        rows.append({
            "match_id": key,
            "match_date": node.get("matchdate") or day.isoformat(),
            "matchnum": node.get("matchnum") or "",
            "league": node.get("league") or "",
            "home": node.get("homename") or "",
            "away": node.get("awayname") or "",
            "home_score": home_score,
            "away_score": away_score,
            "handicap_line": float_or_none(node.get("rangqiu")),
            "win": float_or_none(node.get("win")),
            "draw": float_or_none(node.get("draw")),
            "lost": float_or_none(node.get("lost")),
            "hhad_h": float_or_none(rq[0]) if len(rq) == 3 else None,
            "hhad_d": float_or_none(rq[1]) if len(rq) == 3 else None,
            "hhad_a": float_or_none(rq[2]) if len(rq) == 3 else None,
            "hhad_source": "500_rq" if rq else "",
            # 500.com stores the handicap result in resultspf and the plain result in
            # resultnspf, i.e. the opposite of what the names suggest. Verified against
            # score + line for every stored match.
            "result_spf": node.get("resultnspf") or "",
            "result_hhad": node.get("resultspf") or "",
            "asian_hand": node.get("asianhand") or asian_by_id.get(key, ""),
            "europe_avg": europe_by_id.get(key, ""),
            "overunder": dxq_by_id.get(key, ""),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return rows


def store(connection: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join("?" for _ in columns)
    sql = (f"INSERT OR REPLACE INTO jc_history ({','.join(columns)}) VALUES ({placeholders})")
    connection.executemany(sql, [tuple(row[c] for c in columns) for row in rows])
    connection.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="seconds to wait between days (default 1.5)")
    parser.add_argument("--revisit", action="store_true",
                        help="also re-fetch dates that already have rows")
    args = parser.parse_args()

    connection = sqlite3.connect(DB)
    connection.executescript(SCHEMA)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    existing = {row[0] for row in connection.execute(
        "SELECT DISTINCT match_date FROM jc_history WHERE match_date IS NOT NULL")}
    total = 0
    days_with_data = 0
    skipped = 0
    blocked = 0
    day = start
    while day <= end:
        if not args.revisit and day.isoformat() in existing:
            skipped += 1
            day += timedelta(days=1)
            continue
        try:
            rows = parse_day(day)
            blocked = 0
        except RateLimited:
            blocked += 1
            log(f"  rate limited on {day.isoformat()} ({blocked} in a row), backing off 120s",
                args.quiet)
            time.sleep(120)
            if blocked >= 3:
                log("  stopping: three consecutive challenges, resume later with the same range",
                    args.quiet)
                break
            day += timedelta(days=1)
            continue
        if rows:
            total += store(connection, rows)
            days_with_data += 1
        time.sleep(args.delay)
        if day.day % 10 == 0:
            log(f"  {day.isoformat()}: {len(rows):2d} matches, running total {total}",
                args.quiet)
        day += timedelta(days=1)

    mismatch = 0
    checked = 0
    for row in connection.execute(
            """SELECT home_score, away_score, handicap_line, result_hhad
               FROM jc_history
               WHERE home_score IS NOT NULL AND handicap_line IS NOT NULL
                 AND result_hhad IN ('胜','平','负')"""):
        margin = row[0] - row[1] + row[2]
        expected = "胜" if margin > 0 else ("平" if margin == 0 else "负")
        checked += 1
        if expected != row[3]:
            mismatch += 1
    log(f"validation: {checked} matches re-derived from score + line, {mismatch} mismatched",
        args.quiet)

    with_line = connection.execute(
        "SELECT COUNT(*) FROM jc_history WHERE handicap_line IS NOT NULL").fetchone()[0]
    with_odds = connection.execute(
        "SELECT COUNT(*) FROM jc_history WHERE hhad_h IS NOT NULL").fetchone()[0]
    with_score = connection.execute(
        "SELECT COUNT(*) FROM jc_history WHERE home_score IS NOT NULL").fetchone()[0]
    log(f"done: {total} rows stored over {days_with_data} days "
        f"({start} .. {end}); skipped {skipped} already-collected dates", args.quiet)
    log(f"jc_history total: {connection.execute('SELECT COUNT(*) FROM jc_history').fetchone()[0]} "
        f"| with line {with_line} | with handicap odds {with_odds} | with score {with_score}",
        args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
