#!/usr/bin/env python3
"""Snapshot the jc-daily-report radar page into SQLite.

That page embeds a full per-match payload: a merged median odds series, every bookmaker's
opening and current 1X2 prices, the Asian handicap line and water (opening and current), the
over/under water, a quadrant badge and a signal. It is the only source we have that carries
*opening versus current water*, which is what a movement-based signal needs.

The page is large (about 3 MB) and slow (one request can take over a minute), so it is
fetched once per run and only the extracted fields are stored, keyed by
(match_date, home, away) so repeated runs cannot duplicate a match.

Usage:
    python data_pipeline/collect_jc_radar.py
    python data_pipeline/collect_jc_radar.py --quiet
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
URL = "https://chinjiaqing.github.io/jc-daily-report/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "Chrome/120 Safari/537.36"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS radar_match (
  match_date   TEXT NOT NULL,
  home         TEXT NOT NULL,
  away         TEXT NOT NULL,
  league       TEXT,
  jc_num       TEXT,
  kickoff      TEXT,
  status       TEXT,
  home_score   INTEGER,
  away_score   INTEGER,
  zone         TEXT,
  signal_pred  TEXT,
  signal_id    TEXT,
  open_home    REAL, open_draw REAL, open_away REAL,
  cur_home     REAL, cur_draw REAL, cur_away REAL,
  books_1x2    INTEGER,
  books_moved  INTEGER,
  ah_line_open REAL, ah_line_cur REAL,
  ah_water_open REAL, ah_water_cur REAL,
  ah_books     INTEGER,
  ah_lines_moved INTEGER,
  ou_line_open REAL, ou_line_cur REAL,
  ou_water_open REAL, ou_water_cur REAL,
  ou_books     INTEGER,
  snapshot_at  TEXT,
  PRIMARY KEY (match_date, home, away)
);
CREATE INDEX IF NOT EXISTS idx_radar_date ON radar_match(match_date);
"""


def log(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message, flush=True)


def fetch() -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(URL, headers=HEADERS)
            with opener.open(request, timeout=180) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(10 * attempt)
    raise RuntimeError(f"radar page unavailable: {last}")


def extract_array(html: str, name: str) -> list:
    match = re.search(rf"const\s+{name}\s*=\s*\[", html)
    if not match:
        raise RuntimeError(f"{name} array not found in page")
    index = html.index("[", match.start())
    depth = 0
    in_string = False
    escaped = False
    start = index
    while index < len(html):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    break
        index += 1
    return json.loads(html[start:index + 1])


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def summarise_match(match: dict) -> dict | None:
    home, away, day = match.get("home"), match.get("away"), match.get("date")
    if not (home and away and day):
        return None
    record = {
        "match_date": day, "home": home, "away": away,
        "league": match.get("league") or "", "jc_num": match.get("jc") or "",
        "kickoff": match.get("kickoff") or "", "status": match.get("status") or "",
        "home_score": match.get("sh"), "away_score": match.get("sa"),
        "zone": match.get("zone") or "",
        "signal_pred": (match.get("sig") or {}).get("pred") or "",
        "signal_id": (match.get("sig") or {}).get("id") or "",
    }

    # 1X2 opening and current, averaged across bookmakers
    opens = {"h": [], "d": [], "a": []}
    current = {"h": [], "d": [], "a": []}
    moved = 0
    books = 0
    for book in match.get("eu") or []:
        values = [book.get("h0"), book.get("d0"), book.get("a0"),
                  book.get("h"), book.get("d"), book.get("a")]
        if any(not isinstance(v, (int, float)) or v <= 1 for v in values):
            continue
        books += 1
        for key, opening, now in (("h", book["h0"], book["h"]), ("d", book["d0"], book["d"]),
                                  ("a", book["a0"], book["a"])):
            opens[key].append(opening)
            current[key].append(now)
        if any(abs(book[k] - book[k + "0"]) > 1e-9 for k in ("h", "d", "a")):
            moved += 1
    record.update({
        "open_home": median(opens["h"]), "open_draw": median(opens["d"]),
        "open_away": median(opens["a"]), "cur_home": median(current["h"]),
        "cur_draw": median(current["d"]), "cur_away": median(current["a"]),
        "books_1x2": books, "books_moved": moved,
    })

    # Asian handicap: line and home water
    line_open, line_cur, water_open, water_cur = [], [], [], []
    ah_books = ah_moved = 0
    for book in match.get("ah") or []:
        if not all(isinstance(book.get(k), (int, float))
                   for k in ("line0", "line", "home0", "home")):
            continue
        ah_books += 1
        line_open.append(book["line0"])
        line_cur.append(book["line"])
        water_open.append(book["home0"])
        water_cur.append(book["home"])
        if abs(book["line"] - book["line0"]) > 1e-9:
            ah_moved += 1
    record.update({
        "ah_line_open": median(line_open), "ah_line_cur": median(line_cur),
        "ah_water_open": median(water_open), "ah_water_cur": median(water_cur),
        "ah_books": ah_books, "ah_lines_moved": ah_moved,
    })

    # Over/under: total line and big water
    ou_open, ou_cur, ou_water_open, ou_water_cur = [], [], [], []
    for book in match.get("ou") or []:
        if not all(isinstance(book.get(k), (int, float))
                   for k in ("line0", "line", "big0", "big")):
            continue
        ou_open.append(book["line0"])
        ou_cur.append(book["line"])
        ou_water_open.append(book["big0"])
        ou_water_cur.append(book["big"])
    record.update({
        "ou_line_open": median(ou_open), "ou_line_cur": median(ou_cur),
        "ou_water_open": median(ou_water_open), "ou_water_cur": median(ou_water_cur),
        "ou_books": len(ou_open),
    })
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    html = fetch()
    matches = extract_array(html, "M")
    records = [r for r in (summarise_match(m) for m in matches) if r]
    log(f"radar page: {len(matches)} matches, {len(records)} summarised", args.quiet)

    connection = sqlite3.connect(DB)
    connection.executescript(SCHEMA)
    snapshot_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    columns = list(records[0].keys()) + ["snapshot_at"]
    placeholders = ",".join("?" for _ in columns)
    inserted = 0
    for record in records:
        cursor = connection.execute(
            f"INSERT OR REPLACE INTO radar_match ({','.join(columns)}) VALUES ({placeholders})",
            tuple(record.get(c) for c in columns[:-1]) + (snapshot_at,))
        inserted += cursor.rowcount
    connection.commit()

    total = connection.execute("SELECT COUNT(*) FROM radar_match").fetchone()[0]
    with_ah = connection.execute(
        "SELECT COUNT(*) FROM radar_match WHERE ah_line_open IS NOT NULL").fetchone()[0]
    log(f"stored {inserted} rows; table total {total} ({with_ah} with Asian lines)", args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
