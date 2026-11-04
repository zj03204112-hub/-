#!/usr/bin/env python3
"""Cross-check K League 1 2026 scorelines against the matchesio baseline CSV.

The repository already carries data/auto_results/K_League_1_2026_27.csv, which comes
from matchesio.com and covers 2026-02-28 to 2026-05-12. It is a different provider
from FotMob, so it verifies the early season that the Flashscore feed cannot reach.

Two caveats from the source are handled explicitly:

  * scores are stored as floats ("1.0"), so they are converted to integers before
    comparison instead of string comparison
  * the file contains K League 2 clubs mixed in with K League 1 rows, so rows that do
    not match a collected fixture are reported as out of scope rather than failures

Usage:
    python data_pipeline/crosscheck_kleague1_scores_matchesio.py
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
BASELINE = os.path.join(REPO_ROOT, "data", "auto_results", "K_League_1_2026_27.csv")
PREFIX = "KLEAGUE1_2026_27"
REPORT = os.path.join(SEASON_DIR, f"{PREFIX}_score_crosscheck_matchesio.json")

SUFFIXES = ("fc", "united", "hd", "steelers", "citizen", "motors", "sangmu", "hana", "sk")


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]", "", text)
    for suffix in SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix) + 3:
            return text[: -len(suffix)]
    return text


def same_club(left: str, right: str) -> bool:
    a, b = norm(left), norm(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a or (a[:4] == b[:4] and min(len(a), len(b)) >= 4)


def as_goals(value: str) -> str | None:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return None


def main() -> int:
    batches = []
    for path in sorted(glob.glob(os.path.join(SEASON_DIR, f"{PREFIX}_j*_player_detail_batch_*.json"))):
        batches.append(json.load(open(path, encoding="utf-8")))

    rows = list(csv.DictReader(open(BASELINE, encoding="utf-8-sig")))
    matched = 0
    mismatches, out_of_scope = [], []
    for row in rows:
        home_goals, away_goals = as_goals(row["FTHG"]), as_goals(row["FTAG"])
        if home_goals is None or away_goals is None:
            out_of_scope.append({"row": row["Date"], "reason": "score not numeric"})
            continue
        raw = row["Date"]
        iso = raw if "-" in raw else "-".join(reversed(raw.split("/")))
        try:
            day = datetime.strptime(iso, "%Y-%m-%d")
        except ValueError:
            out_of_scope.append({"row": raw, "reason": "date unparsable"})
            continue
        found = None
        for payload in batches:
            delta = abs((day - datetime.strptime(payload["date"], "%Y-%m-%d")).days)
            if delta > 1:
                continue
            if same_club(row["HomeTeam"], payload["home"]) and same_club(row["AwayTeam"], payload["away"]):
                found = payload
                break
        if not found:
            out_of_scope.append({"fixture": [iso, row["HomeTeam"], row["AwayTeam"]],
                                 "reason": "no collected K League 1 fixture (likely K League 2 row)"})
            continue
        expected = f"{home_goals}-{away_goals}"
        if expected != found["score"]:
            mismatches.append({"fixture": [found["date"], found["home"], found["away"]],
                               "matchesio": expected, "fotmob": found["score"]})
        else:
            matched += 1
            found.setdefault("verification_detail", {})["score_crosscheck_matchesio"] = {
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "secondary_source": "matchesio.com baseline CSV",
                "matched": True, "score": expected,
            }

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "K League 1", "season": "2026",
        "reference_source": "data/auto_results/K_League_1_2026_27.csv (matchesio.com)",
        "reference_rows": len(rows),
        "matched": matched,
        "mismatches": mismatches,
        "out_of_scope": out_of_scope,
        "result": "all matched scorelines agree" if not mismatches else "mismatches found",
    }
    json.dump(report, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(REPORT, "a", encoding="utf-8").write("\n")
    print(f"reference rows: {len(rows)}, matched: {matched}, mismatches: {len(mismatches)}, "
          f"out of scope: {len(out_of_scope)}")
    print(f"report: {os.path.relpath(REPORT, REPO_ROOT)}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
