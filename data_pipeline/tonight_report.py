#!/usr/bin/env python3
"""One combined per-match analysis: 1X2 and handicap side by side.

For every on-sale fixture prints the de-vigged probabilities for both pools, the pick in each,
and a one-line reading derived from the numbers (how much of the 1X2 edge survives the line,
whether the favourite needs a two-goal win, and how much of the handicap safety comes from the
draw). Confidence tiers come from measured histories:

  1X2      favourite probability bucket -> measured does-not-lose / three-way rate
  handicap gap between top two -> measured three-way rate

Usage:
    python data_pipeline/tonight_report.py
    python data_pipeline/tonight_report.py --league 欧冠
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
OUT = os.path.join(REPO_ROOT, "model_validation", "tonight_report.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
SAFE_TIERS = [(0.80, 1.01, 0.970), (0.75, 0.80, 0.938), (0.70, 0.75, 0.917),
              (0.65, 0.70, 0.886), (0.60, 0.65, 0.858), (0.55, 0.60, 0.826),
              (0.50, 0.55, 0.791), (0.45, 0.50, 0.756), (0.40, 0.45, 0.699),
              (0.0, 0.40, 0.674)]
GAP_TIERS = [(0.15, 1.01, 0.555), (0.10, 0.15, 0.451),
             (0.06, 0.10, 0.442), (0.0, 0.06, 0.371)]
HANDICAP_LABELS = ("让胜", "让平", "让负")
ONE_X_TWO_LABELS = ("主胜", "平局", "客胜")


def log(message: str) -> None:
    print(message, flush=True)


def devig(odds: list[float]) -> list[float]:
    inverse = [1.0 / o for o in odds]
    total = sum(inverse)
    return [x / total for x in inverse]


def tier(probability: float) -> float | None:
    for lower, upper, rate in SAFE_TIERS:
        if lower <= probability < upper:
            return rate
    return None


def gap_tier(gap: float) -> float | None:
    for lower, upper, rate in GAP_TIERS:
        if lower <= gap < upper:
            return rate
    return None


def reading(match: dict) -> str:
    p = match["p"]
    hp = match["hp"]
    line = match["line"]
    favourite = "主" if p[0] > p[2] else "客"
    favourite_probability = max(p[0], p[2])
    parts = []
    if favourite_probability >= 0.70:
        parts.append(f"{favourite}队优势明显（{100 * favourite_probability:.0f}%）")
    elif favourite_probability >= 0.55:
        parts.append(f"{favourite}队占优（{100 * favourite_probability:.0f}%）")
    else:
        parts.append(f"双方接近（{favourite}队仅 {100 * favourite_probability:.0f}%）")
    if line is not None and abs(line - round(line)) < 1e-9 and line != 0:
        if line < 0:
            parts.append(f"让{abs(int(line))}球需赢{abs(int(line)) + 1}球以上才能让胜")
        else:
            parts.append(f"受让{int(line)}球，不输太多即可让胜")
    draw_share = hp[1]
    if draw_share >= 0.25:
        parts.append(f"让平占 {100 * draw_share:.0f}%，走盘风险高")
    else:
        parts.append(f"让平仅 {100 * draw_share:.0f}%")
    return "，".join(parts)


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
            had, hhad = item.get("had") or {}, item.get("hhad") or {}
            try:
                odds = [float(had["h"]), float(had["d"]), float(had["a"])]
                h_odds = [float(hhad["h"]), float(hhad["d"]), float(hhad["a"])]
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (KeyError, TypeError, ValueError):
                continue
            if any(o <= 1.0 for o in odds + h_odds):
                continue
            p = devig(odds)
            hp = devig(h_odds)
            favourite = 0 if p[0] > p[2] else 2
            order = sorted(range(3), key=lambda i: -hp[i])
            gap = hp[order[0]] - hp[order[1]]
            row = {
                "num": item.get("matchNumStr"), "league": league,
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "odds": odds, "p": [round(x, 4) for x in p],
                "line": line, "h_odds": h_odds, "hp": [round(x, 4) for x in hp],
                "safe_pick": "主不败" if favourite == 0 else "客不败",
                "safe_probability": round(p[favourite] + p[1], 4),
                "safe_historical": tier(max(p[0], p[2])),
                "one_x_two_pick": ONE_X_TWO_LABELS[favourite],
                "handicap_pick": HANDICAP_LABELS[order[0]],
                "handicap_gap": round(gap, 4),
                "handicap_historical": gap_tier(gap),
            }
            row["reading"] = reading(row)
            rows.append(row)

    rows.sort(key=lambda r: -max(r["p"][0], r["p"][2]))
    record = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "matches": rows}
    json.dump(record, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(rows)} 场\n")
    for row in rows:
        p, hp = row["p"], row["hp"]
        log(f"{row['num']} {row['league']} {row['home']}-{row['away']} ({row['kickoff']})")
        log(f"  胜平负  主 {100 * p[0]:.1f}%  平 {100 * p[1]:.1f}%  客 {100 * p[2]:.1f}%"
            f"   → {row['safe_pick']} {100 * row['safe_probability']:.1f}%"
            f" (历史 {100 * (row['safe_historical'] or 0):.1f}%)")
        log(f"  让球 {row['line']:+.1f}  胜 {100 * hp[0]:.1f}%  平 {100 * hp[1]:.1f}%"
            f"  负 {100 * hp[2]:.1f}%   → {row['handicap_pick']}"
            f" (领先 {row['handicap_gap']:.3f}, 历史 {100 * (row['handicap_historical'] or 0):.1f}%)")
        log(f"  解读: {row['reading']}")
        log("")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
