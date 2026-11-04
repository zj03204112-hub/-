#!/usr/bin/env python3
"""Fill the J1 match level xG / xGA table from FotMob shot data.

The J.League Data Site does not publish xG, and the repository explicitly forbids
using team-season averages as match level values. This script therefore sums the
shot level expected goals of the FotMob public match API per team and stores the
result with an explicit source and status so the value can never be mistaken for an
official figure.

The existing Japanese club names are preserved: the fixture is located through a
small English/J.League name map and matched on date plus club names.

Usage:
    python data_pipeline/fill_j1_match_xg_from_fotmob.py --limit 5
    python data_pipeline/fill_j1_match_xg_from_fotmob.py
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
FIXTURES_JSON = os.path.join(SEASON_DIR, "J1_League_2026_27_fixtures_fotmob.json")
XG_CSV = os.path.join(SEASON_DIR, "J1_League_2026_27_xg_xga.csv")

FOTMOB_BASE = "https://www.fotmob.com/api/data"

# J.League club name used by the repository -> FotMob English name.
CLUB_MAP = {
    "FC東京": "FC Tokyo",
    "京都": "Kyoto Sanga",
    "千葉": "JEF United Chiba",
    "名古屋": "Nagoya Grampus",
    "岡山": "Fagiano Okayama",
    "川崎Ｆ": "Kawasaki Frontale",
    "広島": "Sanfrecce Hiroshima",
    "東京Ｖ": "Tokyo Verdy",
    "柏": "Kashiwa Reysol",
    "横浜FM": "Yokohama F.Marinos",
    "水戸": "Mito HollyHock",
    "浦和": "Urawa Red Diamonds",
    "清水": "Shimizu S-Pulse",
    "町田": "Machida Zelvia",
    "神戸": "Vissel Kobe",
    "福岡": "Avispa Fukuoka",
    "長崎": "V-Varen Nagasaki",
    "鹿島": "Kashima Antlers",
    "Ｃ大阪": "Cerezo Osaka",
    "Ｇ大阪": "Gamba Osaka",
}


def club_key(name: str) -> str:
    """Loose club key: case/punctuation insensitive and dropping a trailing FC."""
    value = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return value[:-2] if value.endswith("fc") else value


def log(message: str) -> None:
    print(message, flush=True)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fixtures = json.load(open(FIXTURES_JSON, encoding="utf-8"))
    rows = list(csv.DictReader(open(XG_CSV, encoding="utf-8-sig")))
    header = list(rows[0].keys())

    by_date: dict[str, list[dict]] = {}
    for fixture in fixtures:
        by_date.setdefault(fixture["date"], []).append(fixture)

    net = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    filled = missing = 0
    samples = []
    targets = rows[:args.limit] if args.limit else rows

    for row in targets:
        day = row["Date"]
        home_en = CLUB_MAP.get(row["HomeTeam"], row["HomeTeam"])
        away_en = CLUB_MAP.get(row["AwayTeam"], row["AwayTeam"])
        match = None
        for candidate in by_date.get(day, []):
            if (club_key(candidate["home"]) == club_key(home_en)
                    and club_key(candidate["away"]) == club_key(away_en)):
                match = candidate
                break
        if not match:
            missing += 1
            log(f"  unmatched: {day} {row['HomeTeam']}({home_en}) vs {row['AwayTeam']}({away_en}) "
                f"candidates={[(c['home'], c['away']) for c in by_date.get(day, [])]}")
            continue
        if args.dry_run:
            log(f"{day} {row['HomeTeam']}-{row['AwayTeam']}: would fetch {match['id']}")
            continue
        detail = get_json(f"{FOTMOB_BASE}/matchDetails?matchId={match['id']}", net)
        content = (detail or {}).get("content") or {}
        shots = ((content.get("shotmap") or {}).get("shots")) or []
        if not shots:
            missing += 1
            continue
        home_xg = sum(float(s.get("expectedGoals") or 0) for s in shots if s.get("teamId") == match["homeId"])
        away_xg = sum(float(s.get("expectedGoals") or 0) for s in shots if s.get("teamId") == match["awayId"])
        row["HomeXG"] = f"{home_xg:.2f}"
        row["AwayXG"] = f"{away_xg:.2f}"
        row["HomeXGA"] = f"{away_xg:.2f}"
        row["AwayXGA"] = f"{home_xg:.2f}"
        row["Source"] = f"https://www.fotmob.com/matches/{match['id']}"
        row["Status"] = "fotmob_shot_level_xg_sum"
        filled += 1
        if len(samples) < 3:
            samples.append((day, row["HomeTeam"], row["AwayTeam"], row["HomeXG"], row["AwayXG"]))
        log(f"{day} {row['HomeTeam']}-{row['AwayTeam']}: xG {home_xg:.2f}-{away_xg:.2f}")
        time.sleep(0.15)

    if args.dry_run:
        return 0

    with open(XG_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "J1 League",
        "season": "2026/27",
        "table": "J1_League_2026_27_xg_xga.csv",
        "method": "sum of FotMob shot level expected goals per team",
        "source": "FotMob public match API (secondary, J.League does not publish xG)",
        "rows_filled": filled,
        "rows_without_fotmob_match": missing,
        "samples": samples,
        "caveat": "xG is a third party derived value, not an official J.League figure; "
                  "xGA is the opponent's summed xG for the same match.",
    }
    path = os.path.join(SEASON_DIR, "J1_League_2026_27_xg_fill_report_"
                                    f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.json")
    json.dump(report, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"filled: {filled}, unmatched: {missing}")
    log(f"report: {os.path.relpath(path, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
