#!/usr/bin/env python3
"""Collect official LaLiga match events and match metadata.

Adds three pieces of information that the normalized player CSVs never carried:

  * goal level events (goal, penalty, own goal, goal disallowed by VAR)
  * disciplinary events (yellow, red, second yellow)
  * match metadata (formation, coach, venue, referee)

Everything here comes from the official LaLiga match page payload, so no third
party is involved. Assists are not published in that payload and are therefore
left blank here; data_pipeline/collect_laliga_shots_and_assists.py fills the
``Assist`` column from a clearly labelled secondary source.

Outputs:
    data/season_match_detail/LaLiga_2026_27_events.csv
    data/season_match_detail/LaLiga_2026_27_match_meta.csv

Usage:
    python data_pipeline/collect_laliga_match_events.py --dry-run
    python data_pipeline/collect_laliga_match_events.py --limit 5
    python data_pipeline/collect_laliga_match_events.py
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
PLAYERS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_players.csv")
EVENTS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_events.csv")
META_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_match_meta.csv")

EVENTS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "Minute", "Clock", "Period",
                 "Team", "Person", "PersonRole", "EventType", "EventDetail",
                 "Assist", "AssistSource", "Source"]
META_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "FormationHome", "FormationAway",
               "ManagerHome", "ManagerAway", "Venue", "Referee", "Source"]

# The official payload labels events by display name; map the ones this project
# keeps and ignore everything else (substitution-adjacent "Tactical"/"Injury" and
# VAR review markers such as "Card upgrade" are not match events of their own).
EVENT_TYPE_BY_NAME = {
    "goal": "goal",
    "header": "goal",
    "free kick": "goal",
    "penalty": "penalty_goal",
    "own": "own_goal",
    "goal awarded": "var_goal_awarded",
    "goal not awarded": "goal_disallowed",
    "yellow": "yellow_card",
    "straight red": "red_card",
    "red": "red_card",
    "second yellow": "second_yellow",
    "yellow red": "second_yellow",
}


def log(message: str) -> None:
    print(message, flush=True)


def opener() -> urllib.request.OpenerDirector:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def fetch_payload(url: str, net, retries: int = 3) -> dict | None:
    last = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                              "Accept-Encoding": "gzip"})
            with net.open(request, timeout=45) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            html = raw.decode("utf-8", "replace")
            found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
            if not found:
                return None
            return json.loads(found.group(1))["props"]["pageProps"]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    log(f"  !! failed: {url} ({last})")
    return None


def fixture_batches() -> dict[tuple[str, str, str], dict]:
    index: dict[tuple[str, str, str], dict] = {}
    for name in sorted(os.listdir(SEASON_DIR)):
        if "player_detail_batch" not in name or not name.endswith(".json"):
            continue
        payload = json.load(open(os.path.join(SEASON_DIR, name), encoding="utf-8"))
        key = (payload.get("date"), payload.get("home"), payload.get("away"))
        if all(key):
            index.setdefault(key, payload)
    return index


def fixture_keys() -> list[tuple[str, str, str]]:
    keys = set()
    with open(PLAYERS_CSV, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            keys.add((row["Date"], row["HomeTeam"], row["AwayTeam"]))
    return sorted(keys)


def normalize_name(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalize_type(collection: str, name: str) -> str | None:
    if collection not in ("goal", "booking", "var"):
        return None
    return EVENT_TYPE_BY_NAME.get((name or "").strip().lower())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    batches = fixture_batches()
    keys = fixture_keys()
    if args.limit:
        keys = keys[:args.limit]
    net = opener()

    event_rows: list[dict] = []
    meta_rows: list[dict] = []
    failed: list[tuple] = []
    counters: dict[str, int] = {}

    for index, key in enumerate(keys, start=1):
        payload = batches.get(key)
        if not payload or not payload.get("official_match_url"):
            log(f"[{index}/{len(keys)}] {key}: no official url, skipped")
            failed.append(key)
            continue
        if args.dry_run:
            log(f"[{index}/{len(keys)}] {key}: would collect")
            continue

        page = fetch_payload(payload["official_match_url"], net)
        if not page:
            failed.append(key)
            continue

        match = page.get("match") or {}
        teams = {match.get("home_team", {}).get("id"): key[1],
                 match.get("away_team", {}).get("id"): key[2]}
        day, home, away = key
        matchday = payload.get("matchday") or (match.get("gameweek") or {}).get("week")
        url = payload["official_match_url"]

        player_names = set()
        for side in ("home", "away"):
            block = ((page.get("data") or {}).get("lineups") or {}).get(side) or {}
            for entry in (block.get("starts") or []) + (block.get("subs") or []):
                person = entry.get("person") or {}
                for value in (person.get("name"), person.get("nickname")):
                    if value:
                        player_names.add(normalize_name(value))

        for event in page.get("events") or []:
            kind = event.get("match_event_kind") or {}
            event_type = normalize_type(kind.get("collection") or "", kind.get("name") or "")
            if not event_type:
                continue
            team_id = ((event.get("lineup") or {}).get("team") or {}).get("id")
            person = (event.get("lineup") or {}).get("person") or {}
            if event_type == "goal_disallowed":
                detail = kind.get("name") or ""
            else:
                detail = kind.get("name") or ""
            event_rows.append({
                "Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": matchday,
                "Minute": "" if event.get("time") is None else str(event.get("time")),
                "Clock": event.get("clock") or "",
                "Period": event.get("period") or "",
                "Team": teams.get(team_id, ""),
                "Person": person.get("name") or person.get("nickname") or "",
                "PersonRole": "player" if normalize_name(person.get("name") or person.get("nickname") or "") in player_names else "staff",
                "EventType": event_type,
                "EventDetail": detail,
                "Assist": "",
                "AssistSource": "",
                "Source": url,
            })
            counters[event_type] = counters.get(event_type, 0) + 1

        managers = []
        for side in ("home", "away"):
            block = ((page.get("data") or {}).get("lineups") or {}).get(side) or {}
            staff = block.get("manager") or []
            managers.append(staff[0]["person"]["name"] if staff else "")

        referee = ""
        for entry in match.get("persons_role") or []:
            role = entry.get("role") or {}
            if role.get("id") == 5 or "referee" in (role.get("name") or "").lower():
                referee = (entry.get("person") or {}).get("name") or ""
                break

        meta_rows.append({
            "Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": matchday,
            "FormationHome": match.get("home_formation") or "",
            "FormationAway": match.get("away_formation") or "",
            "ManagerHome": managers[0], "ManagerAway": managers[1],
            "Venue": (match.get("venue") or {}).get("name") or "",
            "Referee": referee,
            "Source": url,
        })
        log(f"[{index}/{len(keys)}] {key}: {len(page.get('events') or [])} events, "
            f"{match.get('home_formation')}/{match.get('away_formation')}")
        time.sleep(0.15)

    if not args.dry_run:
        event_rows.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"],
                                       int(r["Minute"]) if r["Minute"].isdigit() else 999))
        meta_rows.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"]))
        with open(EVENTS_CSV, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVENTS_HEADER, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(event_rows)
        with open(META_CSV, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=META_HEADER, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(meta_rows)

        log(f"fixtures processed: {len(meta_rows)}, failed: {len(failed)}")
        log(f"events rows: {len(event_rows)} -> {os.path.basename(EVENTS_CSV)}")
        log(f"meta rows: {len(meta_rows)} -> {os.path.basename(META_CSV)}")
        log(f"event types: {json.dumps(counters, ensure_ascii=False)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
