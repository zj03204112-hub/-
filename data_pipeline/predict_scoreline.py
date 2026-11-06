#!/usr/bin/env python3
"""Most likely scoreline per match, straight from the correct-score pool.

The crs pool prices each scoreline individually, so the top scorelines follow from de-vigging
it. The open-ended buckets (胜其它 / 平其它 / 负其它) are kept separate because they do not
correspond to a single score.

Note the pool carries a very heavy overround (about 41% against 13-15% on the main pools), so
the absolute probabilities are diluted; the ranking is the useful part.

Usage:
    python data_pipeline/predict_scoreline.py
    python data_pipeline/predict_scoreline.py --league 欧罗巴 --top 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from devig import power_devig, power_devig_map  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO_ROOT, "model_validation", "scoreline_predictions.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
SCORE_KEY = re.compile(r"^s(\d\d)s(\d\d)$")
OTHER = {"s1sh": "胜其它", "s1sd": "平其它", "s1sa": "负其它"}


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="")
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    url = API + "?" + urllib.parse.urlencode({"poolCode": "had,hhad,crs", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    rows = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            league = item.get("leagueAbbName") or ""
            if args.league and args.league not in league:
                continue
            crs = item.get("crs") or {}
            odds, others = {}, {}
            for key, value in crs.items():
                if key.endswith("f"):
                    continue
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    continue
                if price <= 1.0:
                    continue
                if SCORE_KEY.match(key):
                    odds[key] = price
                elif key in OTHER:
                    others[key] = price
            if len(odds) < 20:
                continue
            inverse = power_devig_map(odds) or {}
            total = sum(inverse.values()) + sum(1.0 / v for v in others.values())
            scores = []
            for key, value in inverse.items():
                match = SCORE_KEY.match(key)
                score = f"{int(match.group(1))}-{int(match.group(2))}"
                scores.append((value / total, score))
            scores.sort(reverse=True)
            rows.append({
                "num": item.get("matchNumStr"), "league": league,
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "top": [{"score": score, "probability": round(p, 4),
                         "fair_odds": round(1.0 / p, 2)} for p, score in scores[:args.top]],
                "top_probability": round(scores[0][0], 4),
                "overround": round(total - 1.0, 4),
            })

    rows.sort(key=lambda r: -r["top_probability"])
    record = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "definition": (
                  "Most likely scorelines from the de-vigged correct-score pool. The open-ended "
                  "'other' buckets are excluded from the ranking because they are not a single "
                  "score. The pool's overround is around 41%, so treat the ranking as the signal "
                  "rather than the absolute percentage."),
              "matches": rows}
    json.dump(record, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(rows)} 场（比分盘走水 {100 * (rows[0]['overround'] if rows else 0):.0f}%）\n")
    print(f"{'编号':8s} {'比赛':20s} {'最可能比分':>8s} {'概率':>6s} {'公平赔率':>8s}  次选 / 三选")
    for row in rows:
        first = row["top"][0]
        rest = "  ".join(f"{x['score']} {100 * x['probability']:.1f}%"
                         for x in row["top"][1:])
        match = f"{row['home'][:7]}-{row['away'][:7]}"
        print(f"{row['num']:8s} {match:20s} {first['score']:>8s} "
              f"{100 * first['probability']:5.1f}% {first['fair_odds']:8.2f}  {rest}")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
