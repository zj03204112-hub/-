#!/usr/bin/env python3
"""Independently cross-check J1 League lineups using shirt numbers.

The J1 tables are transcribed from the official J.League Data Site, and the existing
audit records only compare that site against itself. The obvious hitch for a second
provider is language: the repository stores Japanese player names while FotMob uses
romanised ones, so name matching is not usable.

Shirt numbers are language neutral and both providers publish them, so this script
compares, per team and per fixture:

  * the set of starting XI shirt numbers
  * the set of bench shirt numbers

A fixture only passes when both sets match for both teams. Nothing is rewritten: the
script records the outcome in a report so the existing transcription stays auditable.

Usage:
    python data_pipeline/crosscheck_j1_lineups_fotmob.py --limit 5
    python data_pipeline/crosscheck_j1_lineups_fotmob.py
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
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
MAPPING_CSV = os.path.join(SEASON_DIR, "j1_2026_match_id_mapping.csv")
FIXTURES_JSON = os.path.join(SEASON_DIR, "J1_League_2026_27_fixtures_fotmob.json")
REPORT = os.path.join(SEASON_DIR, "J1_League_2026_27_lineup_crosscheck_fotmob_"
                                  f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.json")

FOTMOB_BASE = "https://www.fotmob.com/api/data"
OFFICIAL = "https://data.j-league.or.jp/SFMS02/?match_card_id={card}"

# J.League club name -> FotMob English name (kept in sync with the xG fill script).
CLUB_MAP = {
    "FC東京": "FC Tokyo", "京都": "Kyoto Sanga", "千葉": "JEF United Chiba",
    "名古屋": "Nagoya Grampus", "岡山": "Fagiano Okayama", "川崎Ｆ": "Kawasaki Frontale",
    "広島": "Sanfrecce Hiroshima", "東京Ｖ": "Tokyo Verdy", "柏": "Kashiwa Reysol",
    "横浜FM": "Yokohama F.Marinos", "水戸": "Mito HollyHock", "浦和": "Urawa Red Diamonds",
    "清水": "Shimizu S-Pulse", "町田": "Machida Zelvia", "神戸": "Vissel Kobe",
    "福岡": "Avispa Fukuoka", "長崎": "V-Varen Nagasaki", "鹿島": "Kashima Antlers",
    "Ｃ大阪": "Cerezo Osaka", "Ｇ大阪": "Gamba Osaka",
}


def log(message: str) -> None:
    print(message, flush=True)


def club_key(name: str) -> str:
    value = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return value[:-2] if value.endswith("fc") else value


def fetch(url: str, net, retries: int = 3) -> str | None:
    last = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                              "Accept-Encoding": "gzip"})
            with net.open(request, timeout=35) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.2 * attempt)
    log(f"  !! fetch failed: {url} ({last})")
    return None


def get_json(url: str, net, retries: int = 3) -> dict | None:
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                            "AppleWebKit/537.36 Chrome/140 Safari/537.36",
                              "Accept": "application/json",
                              "Referer": "https://www.fotmob.com/",
                              "Accept-Encoding": "gzip"})
            with net.open(request, timeout=40) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None


def sections(html: str, code: str) -> list[str]:
    return re.findall(rf"<!--\s*{code}[^>]*?-->(.*?)<!--\s*{code}[^>]*?End", html, re.S)


def shirt_numbers(fragment: str) -> list[str]:
    numbers = []
    for block in re.findall(r'<td class="number">(.*?)</td>', fragment, re.S):
        value = re.sub(r"<[^>]+>", "", block).strip()
        if value.isdigit():
            numbers.append(value)
    return numbers


def team_of(block: str) -> str:
    found = re.search(r'<h3[^>]*>\s*([^<]+)\s*</h3>', block)
    return found.group(1).strip() if found else ""


def fotmob_matches(net) -> dict[str, list[dict]]:
    fixtures = json.load(open(FIXTURES_JSON, encoding="utf-8"))
    by_date: dict[str, list[dict]] = {}
    for fixture in fixtures:
        by_date.setdefault(fixture["date"], []).append(fixture)
    return by_date


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    mapping = list(csv.DictReader(open(MAPPING_CSV, encoding="utf-8-sig")))
    if args.limit:
        mapping = mapping[:args.limit]
    by_date = fotmob_matches(None)
    net = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    results = []
    passed = failed = skipped = 0
    for index, row in enumerate(mapping, start=1):
        card = row["provider_event_id"]
        day = row["match_date"]
        home, away = row["home_team_source"], row["away_team_source"]
        label = f"[{index}/{len(mapping)}] {day} {home}-{away}"

        candidate = None
        for fixture in by_date.get(day, []):
            if (club_key(fixture["home"]) == club_key(CLUB_MAP.get(home, home))
                    and club_key(fixture["away"]) == club_key(CLUB_MAP.get(away, away))):
                candidate = fixture
                break
        if not candidate:
            skipped += 1
            results.append({"fixture": [day, home, away], "status": "fotmob_fixture_not_found"})
            log(f"{label}: no FotMob fixture, skipped")
            continue

        html = fetch(OFFICIAL.format(card=card), net)
        detail = get_json(f"{FOTMOB_BASE}/matchDetails?matchId={candidate['id']}", net)
        if not html or not detail:
            skipped += 1
            results.append({"fixture": [day, home, away], "status": "source_unavailable"})
            continue

        starter_blocks = sections(html, "A5")
        bench_blocks = sections(html, "A6")
        # The official page renders the two teams as consecutive blocks; confirm the
        # order against the club names instead of trusting it blindly.
        names = [team_of(block) for block in starter_blocks]
        def belongs(block_name, short_name):
            a = re.sub(r"[^ぁ-んァ-ヶ一-龥A-Za-z0-9]", "", block_name or "")
            b = re.sub(r"[^ぁ-んァ-ヶ一-龥A-Za-z0-9]", "", short_name or "")
            return bool(a and b and (b[:3] in a or a[:3] in b))
        if len(names) == 2 and belongs(names[0], away) and belongs(names[1], home):
            starter_blocks = [starter_blocks[1], starter_blocks[0]]
            bench_blocks = [bench_blocks[1], bench_blocks[0]]
        official_starters = [shirt_numbers(block) for block in starter_blocks]
        official_bench = [shirt_numbers(block) for block in bench_blocks]
        lineup = (detail.get("content") or {}).get("lineup") or {}
        remote = {side: lineup.get(side) or {} for side in ("homeTeam", "awayTeam")}

        checks = {}
        ok = True
        for side_index, side in enumerate(("homeTeam", "awayTeam")):
            remote_starters = [str(p.get("shirtNumber")) for p in remote[side].get("starters") or []]
            remote_subs = [str(p.get("shirtNumber")) for p in remote[side].get("subs") or []]
            local_starters = official_starters[side_index] if side_index < len(official_starters) else []
            local_bench = official_bench[side_index] if side_index < len(official_bench) else []
            starters_ok = sorted(local_starters) == sorted(remote_starters)
            bench_ok = sorted(local_bench) == sorted(remote_subs)
            checks[side] = {
                "starters_ok": starters_ok, "bench_ok": bench_ok,
                "local_starters": local_starters, "remote_starters": remote_starters,
                "local_bench": local_bench, "remote_bench": remote_subs,
            }
            if not starters_ok:
                checks[side]["starters_only_local"] = sorted(set(local_starters) - set(remote_starters))
                checks[side]["starters_only_remote"] = sorted(set(remote_starters) - set(local_starters))
            if not bench_ok:
                checks[side]["bench_only_local"] = sorted(set(local_bench) - set(remote_subs))
                checks[side]["bench_only_remote"] = sorted(set(remote_subs) - set(local_bench))
            ok = ok and starters_ok and bench_ok

        results.append({"fixture": [day, home, away], "status": "pass" if ok else "mismatch",
                        "checks": checks})
        if ok:
            passed += 1
            log(f"{label}: PASS")
        else:
            failed += 1
            detail_bits = []
            for side, check in checks.items():
                if not check["starters_ok"]:
                    detail_bits.append(f"{side} starters differ "
                                       f"(+{check.get('starters_only_local')} -{check.get('starters_only_remote')})")
                if not check["bench_ok"]:
                    detail_bits.append(f"{side} bench differs "
                                       f"(+{check.get('bench_only_local')} -{check.get('bench_only_remote')})")
            log(f"{label}: MISMATCH " + "; ".join(detail_bits))
        time.sleep(0.15)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "J1 League", "season": "2026/27",
        "method": "shirt-number set comparison per team against FotMob lineups",
        "reference": "FotMob public match API",
        "fixtures": len(mapping),
        "passed": passed, "failed": failed, "skipped": skipped,
        "results": results,
    }
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(REPORT, "a", encoding="utf-8").write("\n")
    log(f"passed {passed}, mismatch {failed}, skipped {skipped}")
    log(f"report: {os.path.relpath(REPORT, REPO_ROOT)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
