#!/usr/bin/env python3
"""Cross-check the Champions League 2026/27 scorelines against Flashscore.

The UCL layer is built from FotMob because UEFA exposes no reachable free endpoint
and Sofascore answers 403. Flashscore publishes an embedded result feed on its
competition page which is reachable from this machine, so it is used here as an
independent provider for the scoreline only. Lineups, cards and shots remain
FotMob data and are still not cross-checked.

After a successful run the batch JSON files are updated with the secondary source
and a score cross-check block, and the status moves from a single-source marker to
one that reflects the verified scoreline.

Usage:
    python data_pipeline/crosscheck_ucl_scores_flashscore.py
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PREFIX = "UCL_2026_27"
FEED_URL = "https://www.flashscore.com/football/europe/champions-league/"
CACHE = os.path.join(SEASON_DIR, f"{PREFIX}_flashscore_results.json")
REPORT = os.path.join(SEASON_DIR, f"{PREFIX}_score_crosscheck_flashscore.json")
NEW_STATUS = ("fotmob_lineups_with_flashscore_score_crosscheck_"
              "secondary_lineup_crosscheck_pending")

TRANSLIT = str.maketrans({"ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
                          "ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "œ": "oe"})
ALIASES = {
    "atlmadrid": "atletico", "atleticomadrid": "atletico", "atletico": "atletico",
    "bodglimt": "bodoglimt", "bodoglimt": "bodoglimt",
    "bayernmunich": "bayernmunchen",
    "psg": "parissaintgermain", "parisstgermain": "parissaintgermain",
}


def norm(value: str) -> str:
    text = (value or "").translate(TRANSLIT)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def club_key(value: str) -> str:
    return ALIASES.get(norm(value), norm(value))


def same_club(left: str, right: str) -> bool:
    a, b = club_key(left), club_key(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a or (a[:5] == b[:5] and min(len(a), len(b)) >= 5)


def fetch_feed() -> list[dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        FEED_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 Chrome/120 Safari/537.36",
                           "Accept-Encoding": "gzip"})
    with opener.open(request, timeout=45) as response:
        import gzip
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    html = raw.decode("utf-8", "replace")
    records = []
    for chunk in html.split("¬~"):
        fields = dict(re.findall(r"([A-Z]{2,3})÷([^¬]*)", chunk))
        if "AA" not in fields or "AE" not in fields or "AF" not in fields:
            continue
        records.append({
            "id": fields.get("AA"), "ts": fields.get("AD"),
            "home": fields.get("AE"), "away": fields.get("AF"),
            "home_score": fields.get("AG"), "away_score": fields.get("AH"),
            "round": fields.get("ER"),
        })
    return records


def normalize_records(records: list[dict]) -> list[dict]:
    """Accept both the compact (hs/as) and verbose (home_score/away_score) shapes."""
    normalized = []
    for record in records:
        normalized.append({
            "id": record.get("id"),
            "ts": record.get("ts"),
            "home": record.get("home"),
            "away": record.get("away"),
            "home_score": record.get("home_score", record.get("hs")),
            "away_score": record.get("away_score", record.get("as")),
            "round": record.get("round"),
        })
    return normalized


def load_or_fetch() -> list[dict]:
    if os.path.exists(CACHE):
        return normalize_records(json.load(open(CACHE, encoding="utf-8")))
    records = fetch_feed()
    json.dump(records, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return normalize_records(records)


def main() -> int:
    records = load_or_fetch()
    finished = []
    for record in records:
        if record["home_score"] in (None, "") or record["away_score"] in (None, ""):
            continue
        finished.append({
            "date": datetime.fromtimestamp(int(record["ts"]), tz=timezone.utc).date().isoformat(),
            "home": record["home"], "away": record["away"],
            "score": f'{record["home_score"]}-{record["away_score"]}',
            "round": record["round"],
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
            unmatched.append({"fixture": [payload["date"], payload["home"], payload["away"]]})
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
        payload["status"] = NEW_STATUS
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "Champions League", "season": "2026-27",
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
