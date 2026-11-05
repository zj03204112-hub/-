#!/usr/bin/env python3
"""High-accuracy picks: favourite does not lose.

Accuracy is the only goal here, so the output uses the formulation with the highest hit rate
that is still a real bet: 主不败 / 客不败 (favourite plus draw). Historical hit rates from
12698 archived matches, by how strong the favourite is:

  favourite probability   share of matches   does not lose   three-way
  0.60 - 0.65                 7.7%              85.8%          64.9%
  0.65 - 0.70                 6.2%              88.6%          72.5%
  0.70 - 0.75                 4.7%              91.7%          76.0%
  0.75 - 0.80                 3.3%              93.8%          78.9%
  0.80 and above              3.4%              97.0%          90.2%

Home favourites hold on slightly better than away ones (92.4% against 90.7%).

Usage:
    python data_pipeline/predict_high_accuracy.py
    python data_pipeline/predict_high_accuracy.py --min-prob 0.70 --all
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
OUT = os.path.join(REPO_ROOT, "model_validation", "high_accuracy_picks.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}

# measured on 12698 matches: (lower, upper, does-not-lose rate, three-way rate)
TIERS = [(0.80, 1.01, 0.970, 0.902),
         (0.75, 0.80, 0.938, 0.789),
         (0.70, 0.75, 0.917, 0.760),
         (0.65, 0.70, 0.886, 0.725),
         (0.60, 0.65, 0.858, 0.649)]


def log(message: str) -> None:
    print(message, flush=True)


def tier_for(probability: float) -> tuple[str, float, float] | None:
    for lower, upper, safe, three in TIERS:
        if lower <= probability < upper:
            return f"{lower:.2f}-{upper if upper < 1 else 1:.2f}", safe, three
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-prob", type=float, default=0.65)
    parser.add_argument("--all", action="store_true", help="ignore the league filter")
    parser.add_argument("--league", default="")
    args = parser.parse_args()

    url = API + "?" + urllib.parse.urlencode({"poolCode": "had", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    picks = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            league = item.get("leagueAbbName") or ""
            if args.league and args.league not in league:
                continue
            had = item.get("had") or {}
            try:
                odds = [float(had["h"]), float(had["d"]), float(had["a"])]
            except (KeyError, TypeError, ValueError):
                continue
            if any(o <= 1.0 for o in odds):
                continue
            inverse = [1.0 / o for o in odds]
            total = sum(inverse)
            probabilities = [x / total for x in inverse]
            favourite = 0 if probabilities[0] > probabilities[2] else 2
            strength = probabilities[favourite]
            if strength < args.min_prob:
                continue
            tier = tier_for(strength)
            safe_side = "主不败" if favourite == 0 else "客不败"
            three_side = ["主胜", "平局", "客胜"][favourite]
            safe_probability = probabilities[favourite] + probabilities[1]
            picks.append({
                "p_home": round(probabilities[0], 4),
                "p_draw": round(probabilities[1], 4),
                "p_away": round(probabilities[2], 4),
                "safe_probability": round(safe_probability, 4),
                "safe_fair_odds": round(1.0 / safe_probability, 3),
                "num": item.get("matchNumStr"), "league": league,
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "odds": odds, "probabilities": [round(p, 4) for p in probabilities],
                "favourite": "home" if favourite == 0 else "away",
                "favourite_probability": round(strength, 4),
                "safe_pick": safe_side, "safe_odds": odds[0] if favourite == 0 else odds[2],
                "three_way_pick": three_side,
                "three_way_odds": odds[favourite],
                "tier": tier[0] if tier else "below",
                "historical_safe_rate": tier[1] if tier else None,
                "historical_three_way_rate": tier[2] if tier else None,
            })

    picks.sort(key=lambda p: -p["favourite_probability"])
    record = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_probability": args.min_prob,
        "definition": (
            "High-accuracy output. The pick is the favourite not losing (主不败/客不败), whose "
            "historical hit rate by strength tier is shown per match. Accuracy is the only "
            "objective here; the odds column is informational, not a profitability claim."),
        "tiers_reference": [
            {"range": "0.80+", "share": "3.4%", "safe": 0.970, "three_way": 0.902},
            {"range": "0.75-0.80", "share": "3.3%", "safe": 0.938, "three_way": 0.789},
            {"range": "0.70-0.75", "share": "4.7%", "safe": 0.917, "three_way": 0.760},
            {"range": "0.65-0.70", "share": "6.2%", "safe": 0.886, "three_way": 0.725},
            {"range": "0.60-0.65", "share": "7.7%", "safe": 0.858, "three_way": 0.649}],
        "picks": picks,
    }
    json.dump(record, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(picks)} 场达到 {args.min_prob:.2f} 门槛\n")
    print(f"{'编号':8s} {'比赛':20s} {'主胜':>6s} {'平':>6s} {'客胜':>6s} "
          f"{'推荐':>6s} {'不败概率':>8s} {'公平赔率':>8s} {'历史命中':>8s}")
    for pick in picks:
        match = f"{pick['home'][:8]}-{pick['away'][:8]}"
        print(f"{pick['num']:8s} {match:20s} "
              f"{100 * pick['p_home']:5.1f}% {100 * pick['p_draw']:5.1f}% "
              f"{100 * pick['p_away']:5.1f}% {pick['safe_pick']:>6s} "
              f"{100 * pick['safe_probability']:7.1f}% {pick['safe_fair_odds']:8.2f} "
              f"{100 * (pick['historical_safe_rate'] or 0):7.1f}%")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
