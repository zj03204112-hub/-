#!/usr/bin/env python3
"""Handicap card for tonight: 让胜 / 让平 / 让负 probabilities per match.

Reads the official jingcai hhad pool (handicap line plus three-way prices), de-vig them, and
prints the full card with the same confidence tiers used elsewhere. The confidence tier comes
from the gap between the top two probabilities, whose accuracy was measured on 5726 archived
matches:

  gap < 0.06        34-40%   (coin flip)
  0.06 - 0.10       44%
  0.10 - 0.15       45%
  0.15 and above    55.5%

Usage:
    python data_pipeline/predict_handicap_card.py
    python data_pipeline/predict_handicap_card.py --league 欧冠
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO_ROOT, "model_validation", "handicap_card.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}

# gap between top two probabilities -> measured three-way hit rate (5726 matches)
GAP_TIERS = [(0.15, 1.01, 0.555), (0.10, 0.15, 0.451),
             (0.06, 0.10, 0.442), (0.0, 0.06, 0.371)]

LABELS = ("让胜", "让平", "让负")


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="")
    args = parser.parse_args()

    url = API + "?" + urllib.parse.urlencode({"poolCode": "had,hhad", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    rows = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            league = item.get("leagueAbbName") or ""
            if args.league and args.league not in league:
                continue
            hhad = item.get("hhad") or {}
            try:
                odds = [float(hhad["h"]), float(hhad["d"]), float(hhad["a"])]
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (KeyError, TypeError, ValueError):
                continue
            if any(o <= 1.0 for o in odds):
                continue
            inverse = [1.0 / o for o in odds]
            total = sum(inverse)
            probabilities = [x / total for x in inverse]
            order = sorted(range(3), key=lambda i: -probabilities[i])
            gap = probabilities[order[0]] - probabilities[order[1]]
            tier = next((t for t in GAP_TIERS if t[0] <= gap < t[1]), None)
            rows.append({
                "num": item.get("matchNumStr"), "league": league,
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "line": line, "odds": odds,
                "probs": [round(p, 4) for p in probabilities],
                "pick": LABELS[order[0]], "pick_probability": round(probabilities[order[0]], 4),
                "gap": round(gap, 4),
                "historical_rate": tier[2] if tier else None,
                "deep_line": abs(line) >= 2,
            })

    rows.sort(key=lambda r: -r["gap"])
    record = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "definition": (
                  "Jingcai handicap three-way from the official hhad pool, de-vigged. The "
                  "historical rate is the measured hit rate for the confidence tier of that "
                  "specific gap, not a guarantee for the individual match."),
              "matches": rows}
    json.dump(record, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(rows)} 场\n")
    print(f"{'编号':8s} {'比赛':20s} {'让球':>5s} {'让胜':>6s} {'让平':>6s} {'让负':>6s} "
          f"{'推荐':>5s} {'领先':>6s} {'档位命中':>8s}")
    for row in rows:
        match = f"{row['home'][:8]}-{row['away'][:8]}"
        line_text = f"{row['line']:+.1f}".replace(".0", "")
        print(f"{row['num']:8s} {match:20s} {line_text:>5s} "
              f"{100 * row['probs'][0]:5.1f}% {100 * row['probs'][1]:5.1f}% "
              f"{100 * row['probs'][2]:5.1f}% {row['pick']:>5s} "
              f"{row['gap']:6.3f} {100 * (row['historical_rate'] or 0):7.1f}%")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
