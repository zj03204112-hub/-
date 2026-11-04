#!/usr/bin/env python3
"""Cross-check the K League 1 2026 scorelines against Flashscore.

football-data.co.uk does not publish the K League, so the independent scoreline
reference is the Flashscore competition feed. The K League 2026 season is split into
a regular phase plus a championship and relegation group, which Flashscore keeps on a
single competition page, so one feed covers every fixture collected by the FotMob
pipeline.

Club names differ between providers ("Ulsan HD FC" vs "Ulsan", "Jeonbuk Hyundai
Motors" vs "Jeonbuk"), so a suffix stripping comparison is used with a one day
tolerance for late kickoffs.

Usage:
    python data_pipeline/crosscheck_kleague1_scores_flashscore.py
"""

from __future__ import annotations

import glob
import gzip
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PREFIX = "KLEAGUE1_2026_27"
FEED_URL = "https://www.flashscore.com/football/south-korea/k-league-1/"
CACHE = os.path.join(SEASON_DIR, f"{PREFIX}_flashscore_results.json")
REPORT = os.path.join(SEASON_DIR, f"{PREFIX}_score_crosscheck_flashscore.json")

SUFFIXES = ("fc", "united", "city", "steelers", "citizen", "motors", "hd", "sk",
            "sangmu", "hana")
ALIASES = {
    "jeonbukhyundaimotors": "jeonbuk",
    "ulsanhd": "ulsan", "ulsan": "ulsan",
    "seoul": "seoul", "fcseoul": "seoul",
    "daejeonhana": "daejeon", "daejeon": "daejeon",
    "gimcheon": "gimcheon", "gimcheonsangmu": "gimcheon",
    "pohang": "pohang", "pohangsteelers": "pohang",
    "gangwon": "gangwon", "incheon": "incheon", "incheonunited": "incheon",
    "jeju": "jeju", "jejuunited": "jeju", "jejusk": "jeju",
    "daegu": "daegu", "daegufc": "daegu", "gwangju": "gwangju", "gwangjufc": "gwangju",
    "suwon": "suwon", "suwonfc": "suwon", "anyang": "anyang",
}


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def club_key(value: str) -> str:
    key = norm(value)
    if key in ALIASES:
        return ALIASES[key]
    for suffix in SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix) + 3:
            key = key[: -len(suffix)]
            break
    return ALIASES.get(key, key)


def same_club(left: str, right: str) -> bool:
    a, b = club_key(left), club_key(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a or (a[:4] == b[:4] and min(len(a), len(b)) >= 4)


def fetch_feed() -> list[dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        FEED_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 Chrome/120 Safari/537.36",
                           "Accept-Encoding": "gzip"})
    with opener.open(request, timeout=45) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    html = raw.decode("utf-8", "replace")
    records = []
    for chunk in html.split("¬~"):
        fields = dict(re.findall(r"([A-Z]{2,3})÷([^¬]*)", chunk))
        if "AA" not in fields or "AE" not in fields or "AF" not in fields:
            continue
        records.append({"id": fields.get("AA"), "ts": fields.get("AD"),
                        "home": fields.get("AE"), "away": fields.get("AF"),
                        "home_score": fields.get("AG"), "away_score": fields.get("AH"),
                        "round": fields.get("ER")})
    return records


def load_or_fetch() -> list[dict]:
    if os.path.exists(CACHE):
        return json.load(open(CACHE, encoding="utf-8"))
    records = fetch_feed()
    json.dump(records, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return records


def main() -> int:
    records = load_or_fetch()
    finished = []
    for record in records:
        if record.get("home_score") in (None, "") or record.get("away_score") in (None, ""):
            continue
        finished.append({
            "date": datetime.fromtimestamp(int(record["ts"]), tz=timezone.utc).date().isoformat(),
            "home": record["home"], "away": record["away"],
            "score": f'{record["home_score"]}-{record["away_score"]}',
            "round": record.get("round"),
        })

    batches = []
    for path in sorted(glob.glob(os.path.join(SEASON_DIR, f"{PREFIX}_j*_player_detail_batch_*.json"))):
        batches.append((path, json.load(open(path, encoding="utf-8"))))

    matched = 0
    mismatches, unmatched = [], []
    for path, payload in batches:
        day = datetime.strptime(payload["date"], "%Y-%m-%d").date()
        found = None
        for record in finished:
            other = datetime.strptime(record["date"], "%Y-%m-%d").date()
            if abs((day - other).days) > 1:
                continue
            if same_club(record["home"], payload["home"]) and same_club(record["away"], payload["away"]):
                found = record
                break
        if not found:
            unmatched.append({"fixture": [payload["date"], payload["home"], payload["away"]],
                              "score": payload["score"]})
            continue
        if found["score"] != payload["score"]:
            mismatches.append({"fixture": [payload["date"], payload["home"], payload["away"]],
                               "flashscore": found["score"], "fotmob": payload["score"]})
            continue
        matched += 1
        payload["verification"] = "fotmob_lineups_with_flashscore_score_crosscheck"
        payload["secondary_score_source"] = FEED_URL
        payload.setdefault("verification_detail", {})["score_crosscheck"] = {
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "secondary_source": FEED_URL,
            "provider": "Flashscore competition feed",
            "matched": True,
            "score": found["score"],
        }
        payload["status"] = ("fotmob_lineups_with_flashscore_score_crosscheck_"
                             "secondary_lineup_pending")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "K League 1", "season": "2026",
        "scope": "scoreline only; lineups, cards and shots remain single provider (FotMob)",
        "reference_source": FEED_URL,
        "batches": len(batches),
        "matched": matched,
        "mismatches": mismatches,
        "unmatched": unmatched,
        "result": ("all matched scorelines agree" if not mismatches and not unmatched
                   else "review required"),
    }
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(REPORT, "a", encoding="utf-8").write("\n")
    print(f"batches: {len(batches)}, matched: {matched}, "
          f"mismatches: {len(mismatches)}, unmatched: {len(unmatched)}")
    print(f"report: {os.path.relpath(REPORT, REPO_ROOT)}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
