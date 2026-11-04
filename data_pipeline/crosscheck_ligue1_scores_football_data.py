#!/usr/bin/env python3
"""Cross-check the Ligue 1 2026/27 scorelines against football-data.co.uk.

The Bundesliga lineup and event layer comes from FotMob, so the scoreline is
independently verified against the football-data.co.uk French season export (F1).

German club names differ between providers ("Bayern Munich" vs "Bayern München",
"Ein Frankfurt" vs "Eintracht Frankfurt", "M'gladbach" vs "Borussia Mönchengladbach"),
so an alias map plus containment matching is used, with a one day tolerance for
kickoffs after midnight.

Usage:
    python data_pipeline/crosscheck_bundesliga_scores_football_data.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PREFIX = "LIGUE1_2026_27"
REPORT = os.path.join(SEASON_DIR, f"{PREFIX}_score_crosscheck_football_data.json")
SOURCE = "https://www.football-data.co.uk/mmz4281/2627/F1.csv"

ALIASES = {
    "parissg": "parissaintgermain",
    "parissaintgermain": "parissaintgermain",
    "psg": "parissaintgermain",
    "marseille": "marseille",
    "olympiquemarseille": "marseille",
    "lyon": "lyon",
    "olympiquelyonnais": "lyon",
    "monaco": "monaco",
    "asmonaco": "monaco",
    "saintetienne": "saintetienne",
    "asse": "saintetienne",
    "parisfc": "parisfc",
    "lemans": "lemans",
}




def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.lower())


def canon(value: str) -> str:
    """Normalize the alias map values too, so entries such as "VfB Stuttgart" match."""
    return norm(ALIASES.get(norm(value), value))


def same_club(left: str, right: str) -> bool:
    a, b = canon(left), canon(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a or (a[:6] == b[:6] and min(len(a), len(b)) >= 6)


def load_batches() -> list[dict]:
    fixtures = []
    for name in os.listdir(SEASON_DIR):
        if not (name.startswith(PREFIX) and "player_detail_batch" in name and name.endswith(".json")):
            continue
        fixtures.append(json.load(open(os.path.join(SEASON_DIR, name), encoding="utf-8")))
    return fixtures


def main() -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(request, timeout=40) as response:
        raw = response.read()
    reference = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "replace"))))

    batches = load_batches()
    used: set[int] = set()
    mismatches, unmatched = [], []
    for row in reference:
        day, month, year = row["Date"].split("/")
        iso = f"{year}-{month}-{day}"
        found = None
        for index, payload in enumerate(batches):
            if index in used:
                continue
            delta = abs((datetime.strptime(iso, "%Y-%m-%d")
                         - datetime.strptime(payload["date"], "%Y-%m-%d")).days)
            if delta > 1:
                continue
            if (same_club(row["HomeTeam"], payload["home"])
                    and same_club(row["AwayTeam"], payload["away"])):
                found = (index, payload)
                break
        if not found:
            unmatched.append({"date": iso, "home": row["HomeTeam"], "away": row["AwayTeam"]})
            continue
        used.add(found[0])
        expected = f'{row["FTHG"]}-{row["FTAG"]}'
        if expected != found[1]["score"]:
            mismatches.append({"date": iso, "home": found[1]["home"], "away": found[1]["away"],
                               "football_data": expected, "fotmob": found[1]["score"]})

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "Ligue 1",
        "season": "2026/27",
        "reference_source": SOURCE,
        "reference_rows": len(reference),
        "batches_available": len(batches),
        "matched": len(used),
        "score_mismatches": mismatches,
        "unmatched_reference_rows": unmatched,
        "result": "all matched scorelines agree" if not mismatches else "mismatches found",
    }
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(REPORT, "a", encoding="utf-8").write("\n")
    print(f"reference rows: {len(reference)}, matched: {len(used)}, "
          f"mismatches: {len(mismatches)}, unmatched: {len(unmatched)}")
    print(f"report: {os.path.relpath(REPORT, REPO_ROOT)}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
