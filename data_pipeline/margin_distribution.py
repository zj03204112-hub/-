#!/usr/bin/env python3
"""Winning-margin distribution (净胜球) from the jingcai correct-score pool.

The user's question is not "which handicap outcome is most likely" but "by how many goals is
the winner most likely to win". The crs pool prices individual scorelines, so the margin
distribution follows directly:

  主赢1球 / 主赢2球 / 主赢3球 / 主赢4+球 / 平局 / 客赢1球 / 客赢2球 / 客赢3球 / 客赢4+球

The three open-ended buckets (胜其它 / 平其它 / 负其它) are distributed proportionally over the
listed margins of their own category, which keeps the total mass intact without inventing a
precise score.

Because a -1 handicap maps exactly onto these margins (让胜 = margin >= 2, 让平 = margin 1,
让负 = margin <= 0), the table also prints what the margin mode implies for the handicap pool.

Usage:
    python data_pipeline/margin_distribution.py
    python data_pipeline/margin_distribution.py --league 欧罗巴
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
OUT = os.path.join(REPO_ROOT, "model_validation", "margin_distribution.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}

SCORE_KEY = re.compile(r"^s(\d\d)s(\d\d)$")
BUCKETS = ["主赢4+", "主赢3", "主赢2", "主赢1", "平局", "客赢1", "客赢2", "客赢3", "客赢4+"]


def log(message: str) -> None:
    print(message, flush=True)


def bucket_for(margin: int) -> str:
    if margin >= 4:
        return "主赢4+"
    if margin == 3:
        return "主赢3"
    if margin == 2:
        return "主赢2"
    if margin == 1:
        return "主赢1"
    if margin == 0:
        return "平局"
    if margin == -1:
        return "客赢1"
    if margin == -2:
        return "客赢2"
    if margin == -3:
        return "客赢3"
    return "客赢4+"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="")
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
            odds = {}
            for key, value in crs.items():
                if key.endswith("f") or not key.startswith("s"):
                    continue
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    continue
                if price > 1.0:
                    odds[key] = price
            if len(odds) < 20:
                continue
            inverse = power_devig_map(odds) or {}
            total = sum(inverse.values())
            probabilities = {k: v / total for k, v in inverse.items()}

            buckets = {name: 0.0 for name in BUCKETS}
            others = {"win": 0.0, "draw": 0.0, "loss": 0.0}
            for key, probability in probabilities.items():
                match = SCORE_KEY.match(key)
                if match:
                    home, away = int(match.group(1)), int(match.group(2))
                    buckets[bucket_for(home - away)] += probability
                elif key == "s1sh":
                    others["win"] += probability
                elif key == "s1sd":
                    buckets["平局"] += probability
                elif key == "s1sa":
                    others["loss"] += probability

            # spread the open-ended buckets over the listed margins of the same category
            home_total = sum(buckets[b] for b in ("主赢1", "主赢2", "主赢3", "主赢4+"))
            for name in ("主赢1", "主赢2", "主赢3", "主赢4+"):
                if home_total > 0:
                    buckets[name] += others["win"] * buckets[name] / home_total
                else:
                    buckets[name] += others["win"] / 4
            away_total = sum(buckets[b] for b in ("客赢1", "客赢2", "客赢3", "客赢4+"))
            for name in ("客赢1", "客赢2", "客赢3", "客赢4+"):
                if away_total > 0:
                    buckets[name] += others["loss"] * buckets[name] / away_total
                else:
                    buckets[name] += others["loss"] / 4

            ordered = sorted(buckets.items(), key=lambda kv: -kv[1])
            # map the margin distribution onto the official handicap line
            hhad = item.get("hhad") or {}
            try:
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (TypeError, ValueError):
                line = None
            derived = None
            if line is not None:
                derived = {"让胜": 0.0, "让平": 0.0, "让负": 0.0}
                for name, probability in buckets.items():
                    if name == "平局":
                        margin = 0
                    elif name.startswith("主赢"):
                        margin = int(name[2:].replace("+", "")) if "+" not in name else 4
                    else:
                        margin = -(int(name[2:].replace("+", "")) if "+" not in name else 4)
                    adjusted = margin + line
                    if adjusted > 0:
                        derived["让胜"] += probability
                    elif adjusted == 0:
                        derived["让平"] += probability
                    else:
                        derived["让负"] += probability

            # two-stage view: first the result, then the margin conditional on that result
            p_home_win = sum(buckets[b] for b in ("主赢1", "主赢2", "主赢3", "主赢4+"))
            p_draw = buckets["平局"]
            p_away_win = sum(buckets[b] for b in ("客赢1", "客赢2", "客赢3", "客赢4+"))
            if p_home_win >= p_away_win:
                winner, winner_probability = "主胜", p_home_win
                conditional = {b: (buckets[b] / p_home_win if p_home_win else 0)
                               for b in ("主赢1", "主赢2", "主赢3", "主赢4+")}
            else:
                winner, winner_probability = "客胜", p_away_win
                conditional = {b: (buckets[b] / p_away_win if p_away_win else 0)
                               for b in ("客赢1", "客赢2", "客赢3", "客赢4+")}
            draw_is_largest = p_draw > max(p_home_win, p_away_win)
            conditional_sorted = sorted(conditional.items(), key=lambda kv: -kv[1])

            rows.append({
                "line": line, "derived_handicap": derived,
                "p_home_win": round(p_home_win, 4), "p_draw": round(p_draw, 4),
                "p_away_win": round(p_away_win, 4),
                "winner": winner, "winner_probability": round(winner_probability, 4),
                "draw_is_largest": draw_is_largest,
                "conditional_margins": {k: round(v, 4) for k, v in conditional_sorted},
                "conditional_mode": conditional_sorted[0][0],
                "conditional_mode_probability": round(conditional_sorted[0][1], 4),
                "num": item.get("matchNumStr"), "league": league,
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "buckets": {k: round(v, 4) for k, v in ordered},
                "mode": ordered[0][0], "mode_probability": round(ordered[0][1], 4),
                "top3": [(k, round(v, 4)) for k, v in ordered[:3]],
                "overround": round(total - 1.0, 4),
            })

    rows.sort(key=lambda r: -r["mode_probability"])
    record = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "definition": (
                  "Winning-margin distribution from the correct-score pool, de-vigged. The "
                  "mode is the single most likely margin, which for a -1 handicap is exactly "
                  "the 让平 outcome."),
              "matches": rows}
    json.dump(record, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(rows)} 场（比分盘走水约 {100 * (rows[0]['overround'] if rows else 0):.0f}%）\n")
    for row in rows:
        buckets = row["buckets"]
        line = (f"主赢1 {100 * buckets['主赢1']:4.1f}%  主赢2 {100 * buckets['主赢2']:4.1f}%  "
                f"主赢3 {100 * buckets['主赢3']:4.1f}%  主赢4+ {100 * buckets['主赢4+']:4.1f}%")
        line2 = (f"平局 {100 * buckets['平局']:4.1f}%  客赢1 {100 * buckets['客赢1']:4.1f}%  "
                 f"客赢2 {100 * buckets['客赢2']:4.1f}%  客赢3 {100 * buckets['客赢3']:4.1f}%  "
                 f"客赢4+ {100 * buckets['客赢4+']:4.1f}%")
        log(f"{row['num']} {row['league']} {row['home']}-{row['away']}"
            + (f"  让{row['line']:+.0f}" if row['line'] is not None else ""))
        log(f"  ① 胜平负: 主胜 {100 * row['p_home_win']:.1f}%  平 {100 * row['p_draw']:.1f}%  "
            f"客胜 {100 * row['p_away_win']:.1f}%  → {row['winner']} {100 * row['winner_probability']:.1f}%"
            + ("  ⚠平局概率最高" if row['draw_is_largest'] else ""))
        cond = row["conditional_margins"]
        log("  ② 若" + row["winner"] + "，赢几球: "
            + "  ".join(f"{k.replace('主赢', '赢').replace('客赢', '赢')} {100 * v:.1f}%"
                        for k, v in cond.items())
            + f"   → 最可能 {row['conditional_mode']} {100 * row['conditional_mode_probability']:.1f}%")
        log(f"  参考(无条件): {row['mode']} {100 * row['mode_probability']:.1f}%")
        log(f"  {line}")
        log(f"  {line2}")
        if row["derived_handicap"]:
            d = row["derived_handicap"]
            log(f"  按让{row['line']:+.0f}线换算: 让胜 {100 * d['让胜']:.1f}%  "
                f"让平 {100 * d['让平']:.1f}%  让负 {100 * d['让负']:.1f}%")
        log("")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
