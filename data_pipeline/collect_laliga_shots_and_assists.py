#!/usr/bin/env python3
"""Collect shot level data (xG, shot location) and assists from FotMob.

The official LaLiga payload does not publish xG, shot coordinates or assists, so
this script uses the FotMob public match API as a clearly labelled secondary
source. Everything it writes carries the provider URL in the ``Source`` column and
the assistant column of the events CSV gets ``AssistSource`` set to FotMob, so the
provenance never gets mixed up with the official fields.

Outputs:
    data/season_match_detail/LaLiga_2026_27_shots.csv
    data/season_match_detail/LaLiga_2026_27_events.csv   (Assist / AssistSource filled)

Usage:
    python data_pipeline/collect_laliga_shots_and_assists.py --dry-run
    python data_pipeline/collect_laliga_shots_and_assists.py --limit 5
    python data_pipeline/collect_laliga_shots_and_assists.py
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PLAYERS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_players.csv")
EVENTS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_events.csv")
SHOTS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_shots.csv")
ASSISTS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_assists.csv")

sys.path.insert(0, os.path.join(REPO_ROOT, "data_pipeline"))
from verify_laliga_player_detail import (  # noqa: E402  (shared name/team matching)
    norm, team_name as repo_team_name, sim, SAME_THRESHOLD,
)

FOTMOB_BASE = "https://www.fotmob.com/api/data"
SHOTS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "Minute", "AddedMinute",
                "Team", "Player", "X", "Y", "XG", "XGOT", "EventType", "ShotType",
                "Situation", "IsBlocked", "IsOnTarget", "IsOwnGoal", "IsFromInsideBox",
                "Source"]


def log(message: str) -> None:
    print(message, flush=True)


def opener() -> urllib.request.OpenerDirector:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


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
            with net.open(request, timeout=45) as response:
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


def fixtures() -> dict[tuple[str, str, str], int | None]:
    """fixture key -> matchday, taken from the player batches."""
    result: dict[tuple[str, str, str], int | None] = {}
    for name in sorted(os.listdir(SEASON_DIR)):
        if "player_detail_batch" not in name or not name.endswith(".json"):
            continue
        payload = json.load(open(os.path.join(SEASON_DIR, name), encoding="utf-8"))
        key = (payload.get("date"), payload.get("home"), payload.get("away"))
        if all(key):
            result.setdefault(key, payload.get("matchday"))
    return result


def matches_for_date(day: str, net, cache: dict[str, dict]) -> dict[tuple[str, str], dict]:
    if day in cache:
        return cache[day]
    payload = get_json(f"{FOTMOB_BASE}/matches?date={day.replace('-', '')}", net)
    found: dict[tuple[str, str], dict] = {}
    if payload:
        for league in payload.get("leagues") or []:
            if (league.get("name") or "") != "LaLiga":
                continue
            for match in league.get("matches") or []:
                home = repo_team_name((match.get("home") or {}).get("name", ""))
                away = repo_team_name((match.get("away") or {}).get("name", ""))
                if home and away:
                    found[(home, away)] = match
    cache[day] = found
    return found


def load_events() -> list[dict]:
    if not os.path.exists(EVENTS_CSV):
        return []
    with open(EVENTS_CSV, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--assists-only", action="store_true",
                        help="refetch assists only, leave the shots CSV untouched")
    args = parser.parse_args()

    catalogue = fixtures()
    keys = sorted(catalogue)
    if args.limit:
        keys = keys[:args.limit]
    net = opener()
    date_cache: dict[str, dict] = {}

    shot_rows: list[dict] = []
    assist_rows: list[dict] = []
    failed: list[tuple] = []

    for index, key in enumerate(keys, start=1):
        day, home, away = key
        if args.dry_run:
            log(f"[{index}/{len(keys)}] {key}: would collect")
            continue
        match = matches_for_date(day, net, date_cache).get((home, away))
        if not match:
            log(f"[{index}/{len(keys)}] {key}: no FotMob fixture, skipped")
            failed.append(key)
            continue
        detail = get_json(f"{FOTMOB_BASE}/matchDetails?matchId={match['id']}", net)
        content = (detail or {}).get("content") or {}
        shots = ((content.get("shotmap") or {}).get("shots")) or []
        source = f"https://www.fotmob.com/matches/{match.get('pageUrl') or match['id']}"
        home_id = (match.get("home") or {}).get("id")
        matchday = catalogue.get(key)

        for shot in shots:
            team = home if shot.get("teamId") == home_id else away
            shot_rows.append({
                "Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": matchday or "",
                "Minute": shot.get("min", ""),
                "AddedMinute": "" if shot.get("minAdded") in (None, 0) else shot.get("minAdded"),
                "Team": team,
                "Player": shot.get("playerName") or shot.get("fullName") or "",
                "X": shot.get("x", ""), "Y": shot.get("y", ""),
                "XG": shot.get("expectedGoals", ""),
                "XGOT": shot.get("expectedGoalsOnTarget", ""),
                "EventType": shot.get("eventType", ""),
                "ShotType": shot.get("shotType", ""),
                "Situation": shot.get("situation", ""),
                "IsBlocked": shot.get("isBlocked", ""),
                "IsOnTarget": shot.get("isOnTarget", ""),
                "IsOwnGoal": shot.get("isOwnGoal", ""),
                "IsFromInsideBox": shot.get("isFromInsideBox", ""),
                "Source": source,
            })

        events = (((content.get("matchFacts") or {}).get("events") or {}).get("events")) or []
        for event in events:
            if event.get("type") != "Goal" or event.get("ownGoal"):
                continue
            assist_name = event.get("assistStr")
            if not assist_name:
                continue
            assist_rows.append({
                "fixture": key,
                "minute": event.get("time"),
                "team": home if event.get("isHome") else away,
                "scorer": (event.get("player") or {}).get("name") or event.get("fullName") or "",
                "assist": assist_name,
                "source": source,
            })

        log(f"[{index}/{len(keys)}] {key}: {len(shots)} shots, "
            f"{sum(1 for a in assist_rows if a['fixture'] == key)} assists")
        time.sleep(0.2)

    if args.dry_run:
        return 0

    if not args.assists_only:
        shot_rows.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"],
                                      int(r["Minute"]) if str(r["Minute"]).isdigit() else 999))
        with open(SHOTS_CSV, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SHOTS_HEADER, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(shot_rows)

    # Fill the official events CSV assist columns, matching on fixture, minute and
    # scorer name (fuzzy, because FotMob uses the usual matchday name).
    event_rows = load_events()
    for row in event_rows:                      # recompute the assist columns from scratch
        row["Assist"] = ""
        row["AssistSource"] = ""
    filled = unmatched = 0
    report_rows = []
    for assist in assist_rows:
        day, home, away = assist["fixture"]
        candidates = []
        for row in event_rows:
            if (row["Date"], row["HomeTeam"], row["AwayTeam"]) != (day, home, away):
                continue
            if row["EventType"] not in ("goal", "penalty_goal"):
                continue
            if row.get("Assist") or row.get("Team") != assist["team"]:
                continue
            try:
                delta = abs(float(row["Minute"]) - float(assist["minute"]))
            except (TypeError, ValueError):
                delta = 99
            candidates.append((sim(row["Person"], assist["scorer"]), delta, row))

        best = None
        if candidates:
            # Name similarity first; stoppage-time goals are published with minute 90
            # by the secondary provider and with 90+ in the official feed, so the
            # minute is only a tie breaker, never a hard filter.
            candidates.sort(key=lambda item: (-item[0], item[1]))
            score, delta, row = candidates[0]
            if score >= SAME_THRESHOLD:
                best = row
            elif len(candidates) == 1 and delta <= 6:
                best = row
            elif score >= 0.80 and delta <= 6:
                best = row
        status = "matched" if best is not None else "unmatched"
        if best is not None:
            best["Assist"] = assist["assist"]
            best["AssistSource"] = assist["source"]
            filled += 1
        else:
            unmatched += 1
        report_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away,
                            "Minute": assist["minute"], "Team": assist["team"],
                            "Scorer": assist["scorer"], "Assist": assist["assist"],
                            "Status": status, "Source": assist["source"]})

    with open(ASSISTS_CSV, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Date", "HomeTeam", "AwayTeam", "Minute",
                                                    "Team", "Scorer", "Assist", "Status", "Source"],
                                lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(sorted(report_rows, key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"], int(r["Minute"]) if str(r["Minute"]).isdigit() else 999)))

    if event_rows:
        header = list(event_rows[0].keys())
        with open(EVENTS_CSV, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(event_rows)

    log(f"fixtures processed: {len(keys) - len(failed)}, failed: {len(failed)}")
    log(f"shot rows: {len(shot_rows)} -> {os.path.basename(SHOTS_CSV)}")
    log(f"assists matched: {filled}, unmatched: {unmatched}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
