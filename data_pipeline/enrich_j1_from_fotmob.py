#!/usr/bin/env python3
"""Enrich the J1 layer with the four fields the official site does not publish.

The J1 tables come from the official J.League Data Site, which has no assists, no
formation, no shot data and no dismissal rows in the parsed sections. FotMob has all
four but names players in romaji, so the two datasets cannot be joined on names.

Shirt numbers are language neutral and both providers publish them, so the bridge is:

    FotMob player id -> shirtNumber -> official roster number -> Japanese name

That lets the enrichment keep the existing Japanese naming instead of mixing scripts.

Fills:
    * match_meta formations
    * red cards and second yellows in the events table
    * assists on existing goal rows
    * a shots table with xG, coordinates and Japanese player names

Nothing is guessed: a player whose shirt number cannot be resolved is reported as
unmapped rather than written with a romaji name.

Usage:
    python data_pipeline/enrich_j1_from_fotmob.py --limit 3
    python data_pipeline/enrich_j1_from_fotmob.py
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
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PREFIX = "J1_League_2026_27"
MAPPING_CSV = os.path.join(SEASON_DIR, "j1_2026_match_id_mapping.csv")
FIXTURES_JSON = os.path.join(SEASON_DIR, "J1_League_2026_27_fixtures_fotmob.json")
EVENTS_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_events.csv")
META_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_match_meta.csv")
SHOTS_CSV = os.path.join(SEASON_DIR, f"{PREFIX}_shots.csv")
REPORT = os.path.join(SEASON_DIR, f"{PREFIX}_fotmob_enrichment_report_"
                                  f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.json")

OFFICIAL = "https://data.j-league.or.jp/SFMS02/?match_card_id={card}"
FOTMOB = "https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"

SHOTS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "Minute", "AddedMinute",
                "Team", "Player", "X", "Y", "XG", "XGOT", "EventType", "ShotType",
                "Situation", "IsBlocked", "IsOnTarget", "IsOwnGoal", "IsFromInsideBox",
                "Source"]

CLUB_MAP = {
    "FC東京": "FC Tokyo", "京都": "Kyoto Sanga", "千葉": "JEF United Chiba",
    "名古屋": "Nagoya Grampus", "岡山": "Fagiano Okayama", "川崎Ｆ": "Kawasaki Frontale",
    "広島": "Sanfrecce Hiroshima", "東京Ｖ": "Tokyo Verdy", "柏": "Kashiwa Reysol",
    "横浜FM": "Yokohama F.Marinos", "水戸": "Mito HollyHock", "浦和": "Urawa Red Diamonds",
    "清水": "Shimizu S-Pulse", "町田": "Machida Zelvia", "神戸": "Vissel Kobe",
    "福岡": "Avispa Fukuoka", "長崎": "V-Varen Nagasaki", "鹿島": "Kashima Antlers",
    "Ｃ大阪": "Cerezo Osaka", "Ｇ大阪": "Gamba Osaka",
}

CARD_KINDS = {"yellowred": "second_yellow", "red": "red_card", "secondyellow": "second_yellow"}


def log(message: str) -> None:
    print(message, flush=True)


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9ぁ-んァ-ヶ一-龥]", "", text)


def club_key(name: str) -> str:
    value = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return value[:-2] if value.endswith("fc") else value


def fetch_text(url: str, net, retries: int = 3) -> str | None:
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
    log(f"  !! official fetch failed: {url} ({last})")
    return None


def fetch_json(url: str, net, retries: int = 3) -> dict | None:
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


def roster(html: str) -> list[dict[str, str]]:
    """Shirt number -> Japanese name for both teams, starters then bench."""
    tables: list[dict[str, str]] = []
    for code in ("A5", "A6"):
        for block in sections(html, code):
            mapping = {}
            for number, name in re.findall(
                    r'<td class="number">\s*(\d+)\s*</td>\s*<td class="name">\s*([^<]*?)\s*</td>', block):
                mapping[number] = re.sub(r"\s+", " ", name).strip()
            tables.append(mapping)
    merged: dict[str, str] = {}
    for table in tables:
        merged.update(table)
    return merged


def side_rosters(html: str) -> tuple[dict[str, str], dict[str, str]]:
    blocks_starters = sections(html, "A5")
    blocks_bench = sections(html, "A6")
    def table(block):
        mapping = {}
        for number, name in re.findall(
                r'<td class="number">\s*(\d+)\s*</td>\s*<td class="name">\s*([^<]*?)\s*</td>', block):
            mapping[number] = re.sub(r"\s+", " ", name).strip()
        return mapping
    home = {}
    away = {}
    for index, block in enumerate(blocks_starters[:2]):
        target = home if index == 0 else away
        target.update(table(block))
    for index, block in enumerate(blocks_bench[:2]):
        target = home if index == 0 else away
        target.update(table(block))
    return home, away


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true",
                        help="process but do not rewrite the CSV files")
    args = parser.parse_args()

    mapping = list(csv.DictReader(open(MAPPING_CSV, encoding="utf-8-sig")))
    if args.limit:
        mapping = mapping[:args.limit]
    fixtures = json.load(open(FIXTURES_JSON, encoding="utf-8"))
    by_date: dict[str, list[dict]] = {}
    for fixture in fixtures:
        by_date.setdefault(fixture["date"], []).append(fixture)

    def row_key(row):
        return (row["Date"], row["HomeTeam"], row["AwayTeam"])

    events = list(csv.DictReader(open(EVENTS_CSV, encoding="utf-8-sig")))
    meta = {row_key(row): row for row in csv.DictReader(open(META_CSV, encoding="utf-8-sig"))}
    meta_header = list(next(iter(meta.values())).keys()) if meta else []

    existing = {(r["Date"], r["HomeTeam"], r["AwayTeam"], r["Team"], r["Person"], r["EventType"])
                for r in events}
    net = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    shots_rows: list[dict] = []
    stats = {"fixtures": 0, "formations": 0, "red_cards": 0, "assists": 0, "shots": 0,
             "unmapped_card": 0, "unmapped_shot": 0, "unmapped_assist": 0, "skipped": 0}

    for index, row in enumerate(mapping, start=1):
        day = row["match_date"]
        home, away = row["home_team_source"], row["away_team_source"]
        label = f"[{index}/{len(mapping)}] {day} {home}-{away}"

        fixture = None
        for candidate in by_date.get(day, []):
            if (club_key(candidate["home"]) == club_key(CLUB_MAP.get(home, home))
                    and club_key(candidate["away"]) == club_key(CLUB_MAP.get(away, away))):
                fixture = candidate
                break
        if not fixture:
            stats["skipped"] += 1
            continue
        html = fetch_text(OFFICIAL.format(card=row["provider_event_id"]), net)
        detail = fetch_json(FOTMOB.format(match_id=fixture["id"]), net)
        if not html or not detail:
            stats["skipped"] += 1
            continue
        stats["fixtures"] += 1

        home_roster, away_roster = side_rosters(html)
        lineups = (detail.get("content") or {}).get("lineup") or {}
        sides = (("homeTeam", home, home_roster), ("awayTeam", away, away_roster))

        # formations
        key = (day, home, away)
        if key in meta:
            changed = False
            for side, _, _ in sides:
                formation = ((lineups.get(side) or {}).get("formation") or "").replace("-", "")
                column = "FormationHome" if side == "homeTeam" else "FormationAway"
                if formation and not meta[key][column]:
                    meta[key][column] = formation
                    changed = True
            if changed:
                stats["formations"] += 1

        # player id -> shirt number per side
        id_to_number: dict[str, str] = {}
        for side, _, _ in sides:
            block = lineups.get(side) or {}
            for player in (block.get("starters") or []) + (block.get("subs") or []):
                if player.get("id") is not None and player.get("shirtNumber") is not None:
                    id_to_number[str(player["id"])] = str(player["shirtNumber"])

        events_fragment = ((((detail.get("content") or {}).get("matchFacts") or {})
                            .get("events") or {}).get("events")) or []
        for event in events_fragment:
            if event.get("type") != "Card":
                continue
            kind = CARD_KINDS.get(norm(event.get("card")))
            if not kind:
                continue
            team = home if event.get("isHome") else away
            roster = home_roster if event.get("isHome") else away_roster
            player = event.get("player") or {}
            number = id_to_number.get(str(player.get("id")))
            japanese = roster.get(number) if number else None
            if not japanese:
                stats["unmapped_card"] += 1
                continue
            dedupe = (day, home, away, team, japanese, kind)
            if dedupe in existing:
                continue
            existing.add(dedupe)
            events.append({
                "Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": "",
                "Minute": "" if event.get("time") is None else str(event.get("time")),
                "Clock": event.get("timeStr") or "", "Period": "",
                "Team": team, "Person": japanese, "PersonRole": "player",
                "EventType": kind, "EventDetail": event.get("card") or "",
                "Assist": "", "AssistSource": "",
                "Source": f"https://www.fotmob.com/matches/{fixture['id']}",
            })
            stats["red_cards"] += 1

        # assists on existing goal rows
        for side, team, roster in sides:
            block = lineups.get(side) or {}
            team_players = {norm(p.get("name")): p for p in (block.get("starters") or []) + (block.get("subs") or [])}
            for event in events_fragment:
                if event.get("type") != "Goal":
                    continue
                if bool(event.get("isHome")) != (side == "homeTeam"):
                    continue
                assist_text = event.get("assistStr") or ""
                if not assist_text:
                    continue
                assist_name = assist_text.replace("assist by", "").strip()
                match = team_players.get(norm(assist_name))
                if not match:
                    stats["unmapped_assist"] += 1
                    continue
                japanese = roster.get(str(match.get("shirtNumber")))
                if not japanese:
                    stats["unmapped_assist"] += 1
                    continue
                scorer_number = id_to_number.get(str((event.get("player") or {}).get("id")))
                scorer = roster.get(scorer_number) if scorer_number else None
                if not scorer:
                    stats["unmapped_assist"] += 1
                    continue
                minute = event.get("time")
                for target in events:
                    if (target["Date"], target["HomeTeam"], target["AwayTeam"]) != key:
                        continue
                    if target["EventType"] not in ("goal", "penalty_goal"):
                        continue
                    if target["Team"] != team or target["Person"] != scorer:
                        continue
                    try:
                        if minute is None or abs(int(target["Minute"]) - int(minute)) > 2:
                            continue
                    except (TypeError, ValueError):
                        continue
                    if target["Assist"]:
                        continue
                    target["Assist"] = japanese
                    target["AssistSource"] = f"https://www.fotmob.com/matches/{fixture['id']}"
                    stats["assists"] += 1
                    break

        # shots
        shots = (((detail.get("content") or {}).get("shotmap") or {}).get("shots")) or []
        for shot in shots:
            team = home if shot.get("teamId") == fixture["homeId"] else away
            roster = home_roster if team == home else away_roster
            number = id_to_number.get(str(shot.get("playerId")))
            japanese = roster.get(number) if number else None
            if not japanese:
                stats["unmapped_shot"] += 1
                continue
            shots_rows.append({
                "Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": "",
                "Minute": shot.get("min", ""),
                "AddedMinute": "" if shot.get("minAdded") in (None, 0) else shot.get("minAdded"),
                "Team": team, "Player": japanese,
                "X": shot.get("x", ""), "Y": shot.get("y", ""),
                "XG": shot.get("expectedGoals", ""), "XGOT": shot.get("expectedGoalsOnTarget", ""),
                "EventType": shot.get("eventType", ""), "ShotType": shot.get("shotType", ""),
                "Situation": shot.get("situation", ""), "IsBlocked": shot.get("isBlocked", ""),
                "IsOnTarget": shot.get("isOnTarget", ""), "IsOwnGoal": shot.get("isOwnGoal", ""),
                "IsFromInsideBox": shot.get("isFromInsideBox", ""),
                "Source": f"https://www.fotmob.com/matches/{fixture['id']}",
            })
            stats["shots"] += 1
        if index % 20 == 0:
            log(f"  progress {index}/{len(mapping)} shots={stats['shots']}")

    if args.dry_run:
        log(f"dry run stats: {stats}")
        return 0

    # write back
    event_header = list(events[0].keys())
    events.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"],
                               int(r["Minute"]) if str(r["Minute"]).isdigit() else 999))
    with open(EVENTS_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=event_header, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(events)

    with open(META_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=meta_header, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(sorted(meta.values(), key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"])))

    shots_rows.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"],
                                   int(r["Minute"]) if str(r["Minute"]).isdigit() else 999))
    with open(SHOTS_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHOTS_HEADER, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(shots_rows)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "J1 League", "season": "2026/27",
        "method": "FotMob player id -> shirt number -> official Japanese roster name",
        "stats": stats,
        "fields_added": ["formation", "red cards", "assists", "shots with xG"],
        "unmapped_policy": "rows whose shirt number cannot be resolved are skipped and counted, "
                           "never written with a romaji name",
    }
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(REPORT, "a", encoding="utf-8").write("\n")
    log(f"stats: {stats}")
    log(f"report: {os.path.relpath(REPORT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
