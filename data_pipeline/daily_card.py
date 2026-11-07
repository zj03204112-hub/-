#!/usr/bin/env python3
"""The daily card, in the shape the user actually reads.

Every fixture is printed as four short lines rather than a wall of numbers:

  谁赢   the 1X2 probabilities, or an explicit note that there is no clear direction
  让球   the handicap pick, its probability, the gap to the runner-up and the tier
  几球   the most likely winning margin and the most likely total-goals band
  比分   the three likeliest scorelines with their probabilities, marked as dispersed

The scoreline line is deliberately plural. A single predicted score is the least informative
number on the card: even the modal scoreline is only about 12%, and in an even match it is 1-1
almost by construction, so printing one score invites false confidence.

Tiers come from the measured history of the gap between the top two handicap probabilities:
  A  gap >= 0.30   59.8% hit rate on 41.9% of matches
  B  0.15 - 0.30   55.4%
  C  below 0.15    51.5%  (usually best skipped)

Usage:
    python data_pipeline/daily_card.py
    python data_pipeline/daily_card.py --prefix 周五
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from devig import power_devig, power_devig_map

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO_ROOT, "model_validation", "daily_card.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
SCORE_KEY = re.compile(r"^s(\d\d)s(\d\d)$")
HANDICAP_LABELS = ("让胜", "让平", "让负")


def log(message: str) -> None:
    print(message, flush=True)


def tier_of(gap: float) -> str:
    if gap >= 0.30:
        return "A"
    if gap >= 0.15:
        return "B"
    return "C"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()

    url = API + "?" + urllib.parse.urlencode({"poolCode": "had,hhad,crs", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    cards = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            number = item.get("matchNumStr") or ""
            if args.prefix and not number.startswith(args.prefix):
                continue
            had, hhad, crs = item.get("had") or {}, item.get("hhad") or {}, item.get("crs") or {}
            try:
                one_x_two = power_devig([float(had["h"]), float(had["d"]), float(had["a"])])
                three = power_devig([float(hhad["h"]), float(hhad["d"]), float(hhad["a"])])
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (KeyError, TypeError, ValueError):
                continue
            if not one_x_two or not three:
                continue

            score_odds = {}
            for key, value in crs.items():
                if key.endswith("f"):
                    continue
                match = SCORE_KEY.match(key)
                if not match:
                    continue
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    continue
                if price > 1.0:
                    score_odds[(int(match.group(1)), int(match.group(2)))] = price
            scorelines = []
            margins = {}
            totals = defaultdict(float)
            if len(score_odds) >= 20:
                probabilities = power_devig_map(score_odds) or {}
                scorelines = sorted(probabilities.items(), key=lambda kv: -kv[1])[:3]
                favourite = 0 if one_x_two[0] > one_x_two[2] else 2
                for (home, away), probability in probabilities.items():
                    margin = home - away if favourite == 0 else away - home
                    if margin > 0:
                        margins[min(margin, 4)] = margins.get(min(margin, 4), 0.0) + probability
                    total = home + away
                    band = "0-1" if total <= 1 else ("2-3" if total <= 3 else "4+")
                    totals[band] += probability
                scale = sum(margins.values())
                margins = {k: v / scale for k, v in margins.items()} if scale else {}
                conditional_margins = dict(margins)

            order = sorted(range(3), key=lambda i: -three[i])
            gap = three[order[0]] - three[order[1]]
            favourite = 0 if one_x_two[0] > one_x_two[2] else 2
            spf_gap = abs(one_x_two[0] - one_x_two[2])
            cards.append({
                "num": number, "league": item.get("leagueAbbName"),
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "one_x_two": [round(x, 4) for x in one_x_two],
                "spf_pick": ["主胜", "平局", "客胜"][favourite],
                "spf_probability": round(one_x_two[favourite], 4),
                "spf_clear": spf_gap >= 0.20,
                "safe_pick": "主不败" if favourite == 0 else "客不败",
                "safe_probability": round(one_x_two[favourite] + one_x_two[1], 4),
                "risk_probability": round(one_x_two[2 if favourite == 0 else 0], 4),
                "double_chance_odds": (round(1.0 / (1.0 / float((had.get("h") if favourite == 0
                                                                  else had.get("a")))
                                                  + 1.0 / float(had["d"])), 3)
                                        if had.get("d") else None),
                "line": line,
                "handicap": [round(x, 4) for x in three],
                "handicap_pick": HANDICAP_LABELS[order[0]],
                "handicap_probability": round(three[order[0]], 4),
                "gap": round(gap, 4), "tier": tier_of(gap),
                "margin_top": (max(margins.items(), key=lambda kv: kv[1]) if margins else None),
                "margins_conditional": ({str(k): round(v, 4) for k, v in sorted(margins.items())}
                                        if margins else None),
                "total_band": (max(totals.items(), key=lambda kv: kv[1]) if totals else None),
                "scorelines": [(f"{h}-{a}", round(p, 4)) for (h, a), p in scorelines],
                "draw_handicap": round(three[1], 4),
            })

    cards.sort(key=lambda c: (-c["gap"], -c["handicap_probability"]))
    json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "cards": cards}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(cards)} 场\n")
    for card in cards:
        p = card["one_x_two"]
        direction = card["spf_pick"] if card["spf_clear"] else "无明确方向"
        log(f"{card['num']} {card['league']} {card['home']}-{card['away']}  让{card['line']:+.0f}")
        if card["safe_pick"] == "主不败":
            parts = f"主胜 {100 * p[0]:.1f}% + 平 {100 * p[1]:.1f}%"
            risk = f"客胜 {100 * p[2]:.1f}%"
        else:
            parts = f"客胜 {100 * p[2]:.1f}% + 平 {100 * p[1]:.1f}%"
            risk = f"主胜 {100 * p[0]:.1f}%"
        log(f"  推荐  {card['safe_pick']} {100 * card['safe_probability']:.1f}%"
            f"   = {parts}   | 风险：{risk} 则输")
        log(f"  让球  {card['handicap_pick']} {100 * card['handicap_probability']:.1f}%"
            f"  (领先 {100 * card['gap']:.1f}pp)  [{card['tier']} 级]"
            + (f"  让平 {100 * card['draw_handicap']:.1f}%" if card["draw_handicap"] >= 0.26 else ""))
        margin_text = (f"净胜 {card['margin_top'][0]} 球 {100 * card['margin_top'][1]:.0f}%"
                       if card["margin_top"] else "无比分数据")
        total_text = (f"总进球 {card['total_band'][0]} 球 {100 * card['total_band'][1]:.0f}%"
                      if card["total_band"] else "")
        log(f"  几球  {margin_text}   {total_text}")
        if card["scorelines"]:
            log("  比分  " + " / ".join(f"{s} {100 * v:.0f}%" for s, v in card["scorelines"])
                + "   (分散，仅供参考)")
        log("")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
