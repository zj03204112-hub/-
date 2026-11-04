#!/usr/bin/env python3
"""Collect J1 League match events and metadata from the official J.League Data Site.

The existing J1 tables (players / substitutions) are built from the official J.League
Data Site and use Japanese club and player names. This script extends the same
source with the pieces that were still missing and keeps that naming convention:

    J1_League_2026_27_events.csv       goals and cards (official, Japanese names)
    J1_League_2026_27_match_meta.csv   managers, venue, referee, attendance

Assists are not published on the official match report page and stay blank, which
matches the repository rule that missing values must remain empty instead of being
inferred.

Usage:
    python data_pipeline/collect_j1_official_events.py --limit 3
    python data_pipeline/collect_j1_official_events.py
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
PLAYERS_CSV = os.path.join(SEASON_DIR, "J1_League_2026_27_players.csv")
EVENTS_CSV = os.path.join(SEASON_DIR, "J1_League_2026_27_events.csv")
META_CSV = os.path.join(SEASON_DIR, "J1_League_2026_27_match_meta.csv")

MATCH_URL = "https://data.j-league.or.jp/SFMS02/?match_card_id={card}"

EVENTS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "Minute", "Clock", "Period",
                 "Team", "Person", "PersonRole", "EventType", "EventDetail",
                 "Assist", "AssistSource", "Source"]
META_HEADER = ["Date", "HomeTeam", "AwayTeam", "Matchday", "FormationHome", "FormationAway",
               "ManagerHome", "ManagerAway", "Venue", "Referee", "Attendance", "Source"]

CARD_LABELS = {"警告": "yellow_card", "退場": "red_card"}


def log(message: str) -> None:
    print(message, flush=True)


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


def sections(html: str, code: str) -> list[str]:
    """Every start/end block for a section code; A5-A8 and A10 appear twice."""
    return re.findall(rf"<!--\s*{code}[^>]*?-->(.*?)<!--\s*{code}[^>]*?End", html, re.S)


def section(html: str, code: str) -> str:
    return "\n".join(sections(html, code))


def cells(fragment: str) -> list[str]:
    values = re.findall(r"<td[^>]*>(.*?)</td>", fragment, re.S)
    cleaned = []
    for value in values:
        text = re.sub(r"<[^>]+>", " ", value)
        text = text.replace("&nbsp;", " ").strip()
        cleaned.append(re.sub(r"\s+", " ", text))
    return cleaned


def is_minute(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}'\s*(\+\s*\d+)?|\d{1,3}'\+\d+", value.strip()))


def pair_events(fragment: str, marker: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (left, right) as (player, minute) pairs around a headline marker.

    The marker ("得点", "警告", "退場") is rendered as a headline element, not as a
    table cell, so the fragment is split on the raw text before cells are read.
    """
    if marker not in fragment:
        return [], []
    head, tail = fragment.split(marker, 1)
    left, right = cells(head), cells(tail)
    pairs_left, pairs_right = [], []
    i = 0
    while i < len(left) - 1:
        if is_minute(left[i + 1]) and left[i] and not is_minute(left[i]):
            pairs_left.append((left[i], left[i + 1]))
            i += 2
        else:
            i += 1
    i = 0
    while i < len(right) - 1:
        if is_minute(right[i]) and right[i + 1] and not is_minute(right[i + 1]):
            pairs_right.append((right[i + 1], right[i]))
            i += 2
        else:
            i += 1
    return pairs_left, pairs_right


def pairs_in_table(fragment: str) -> list[tuple[str, str]]:
    flat = cells(fragment)
    pairs = []
    for index, value in enumerate(flat):
        if is_minute(value) and index > 0:
            name = flat[index - 1]
            if name and not is_minute(name):
                pairs.append((name, value))
    return pairs


def card_events(fragment: str) -> list[tuple[int, str, str, str]]:
    """Return (side, label, player, minute); side 0 = home, 1 = away."""
    results = []
    for block in re.split(r"<h4[^>]*>", fragment)[1:]:
        label_match = re.match(r"\s*([^<]+)</h4>", block)
        label = label_match.group(1).strip() if label_match else ""
        if label not in CARD_LABELS:
            continue
        body = block[label_match.end():] if label_match else block
        tables = re.findall(r'<div class="two-column-table-base">(.*?)</div>', body, re.S)
        sides = tables if tables else [body]
        for side_index, table in enumerate(sides[:2]):
            for player, minute in pairs_in_table(table):
                results.append((side_index, label, player, minute))
    return results


def minute_value(text: str) -> str:
    digits = re.match(r"(\d+)", text.strip())
    return digits.group(1) if digits else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mapping = list(csv.DictReader(open(MAPPING_CSV, encoding="utf-8-sig")))
    if args.limit:
        mapping = mapping[:args.limit]
    net = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    event_rows: list[dict] = []
    meta_rows: list[dict] = []
    failed: list[str] = []
    goal_mismatch: list[dict] = []
    totals = {"yellow_card": 0, "red_card": 0, "goal": 0}

    for index, row in enumerate(mapping, start=1):
        card = row["provider_event_id"]
        day = row["match_date"]
        home, away = row["home_team_source"], row["away_team_source"]
        url = MATCH_URL.format(card=card)
        label = f"[{index}/{len(mapping)}] {day} {home}-{away}"
        if args.dry_run:
            log(f"{label}: would collect")
            continue
        html = fetch(url, net)
        if not html:
            failed.append(card)
            continue

        goal_left, goal_right = pair_events(section(html, "A2"), "得点")
        for player, minute in goal_left:
            event_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": "",
                               "Minute": minute_value(minute), "Clock": minute.strip(),
                               "Period": "", "Team": home, "Person": player,
                               "PersonRole": "player", "EventType": "goal", "EventDetail": "得点",
                               "Assist": "", "AssistSource": "", "Source": url})
            totals["goal"] += 1
        for player, minute in goal_right:
            event_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": "",
                               "Minute": minute_value(minute), "Clock": minute.strip(),
                               "Period": "", "Team": away, "Person": player,
                               "PersonRole": "player", "EventType": "goal", "EventDetail": "得点",
                               "Assist": "", "AssistSource": "", "Source": url})
            totals["goal"] += 1

        expected_home, expected_away = int(float(row["home_goals"])), int(float(row["away_goals"]))
        if (len(goal_left), len(goal_right)) != (expected_home, expected_away):
            goal_mismatch.append({"fixture": [day, home, away], "parsed": [len(goal_left), len(goal_right)],
                                  "expected": [expected_home, expected_away]})

        card_blocks = sections(html, "A8")
        parsed_cards = []
        for side_index, block in enumerate(card_blocks[:2]):
            for _, card_label, player, minute in card_events(block):
                parsed_cards.append((side_index, card_label, player, minute))
        for side_index, card_label, player, minute in parsed_cards:
            event_type = CARD_LABELS[card_label]
            event_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": "",
                               "Minute": minute_value(minute), "Clock": minute.strip(),
                               "Period": "", "Team": home if side_index == 0 else away,
                               "Person": player, "PersonRole": "player",
                               "EventType": event_type, "EventDetail": card_label,
                               "Assist": "", "AssistSource": "", "Source": url})
            totals[event_type] += 1

        managers = []
        for block in sections(html, "A10")[:2]:
            names = [c for c in cells(block) if c and re.search(r"[ぁ-んァ-ヶ一-龥]", c)]
            managers.append(names[0] if names else "")
        info_fragment = section(html, "A12")
        referee_match = re.search(r"<dt>\s*主審\s*</dt>\s*<dd>\s*([^<]+)</dd>", info_fragment)
        referee = referee_match.group(1).strip() if referee_match else ""
        info_cells = [c for c in cells(info_fragment) if c]
        venue = ""
        for candidate in info_cells:
            if re.fullmatch(r"\d{4}/\d{2}/\d{2}", candidate):
                continue
            if re.fullmatch(r"\d{1,2}:\d{2}", candidate):
                continue
            if re.fullmatch(r"[\d.,%]+", candidate):
                continue
            if candidate in ("晴", "曇", "雨", "雪", "曇り", "小雨"):
                continue
            if "人" in candidate:
                continue
            if re.search(r"[ぁ-んァ-ヶ一-龥]", candidate) or re.search(r"[A-Za-z]", candidate):
                venue = candidate
                break
        attendance = ""
        for candidate in info_cells:
            match = re.fullmatch(r"([\d,]+)\s*人?", candidate or "")
            if match and "," in candidate:
                attendance = match.group(1)
                break

        meta_rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "Matchday": "",
                          "FormationHome": "", "FormationAway": "",
                          "ManagerHome": managers[0] if managers else "",
                          "ManagerAway": managers[1] if len(managers) > 1 else "",
                          "Venue": venue, "Referee": referee, "Attendance": attendance,
                          "Source": url})
        log(f"{label}: goals {len(goal_left)}-{len(goal_right)} "
            f"(expected {expected_home}-{expected_away}), cards "
            f"{sum(1 for r in event_rows if r['Source'] == url and r['EventType'].endswith('card'))}")
        time.sleep(0.15)

    if args.dry_run:
        return 0

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

    log(f"matches processed: {len(meta_rows)}, failed: {len(failed)}")
    log(f"events: {totals}, rows: {len(event_rows)}")
    log(f"goal count mismatches: {len(goal_mismatch)}")
    for mismatch in goal_mismatch[:5]:
        log(f"  {mismatch}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
