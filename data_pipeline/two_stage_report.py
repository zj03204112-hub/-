#!/usr/bin/env python3
"""Two-stage report: who wins, then by how many goals.

This is the format the user actually acts on, so each fixture is printed as two decisions:

  1. who wins      the de-vigged 1X2 probabilities and the pick
  2. by how many   the margin distribution *conditional on that pick winning*, taken from the
                   correct-score pool, with each margin labelled by the handicap outcome it
                   produces on the official line

The second mapping is the point: with a -1 line, a one-goal win is 让平 and anything from two
goals up is 让胜, so the conditional margin table tells you which handicap outcomes are live and
with what probability, without pretending that 让平 should ever be the single most likely of the
three.

Usage:
    python data_pipeline/two_stage_report.py
    python data_pipeline/two_stage_report.py --league 欧罗巴
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from devig import power_devig, power_devig_map  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "two_stage_report.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
SCORE_KEY = re.compile(r"^s(\d\d)s(\d\d)$")


def log(message: str) -> None:
    print(message, flush=True)


def devig(values: list[float]) -> list[float] | None:
    return power_devig(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="")
    args = parser.parse_args()

    url = API + "?" + urllib.parse.urlencode(
        {"poolCode": "had,hhad,crs", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    rows = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            league = item.get("leagueAbbName") or ""
            if args.league and args.league not in league:
                continue
            had, hhad, crs = item.get("had") or {}, item.get("hhad") or {}, item.get("crs") or {}
            try:
                one_x_two = devig([float(had["h"]), float(had["d"]), float(had["a"])])
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (KeyError, TypeError, ValueError):
                continue
            if not one_x_two:
                continue

            score_odds, other_odds = {}, {}
            for key, value in crs.items():
                if key.endswith("f"):
                    continue
                try:
                    price = float(value)
                except (TypeError, ValueError):
                    continue
                if price <= 1.0:
                    continue
                match = SCORE_KEY.match(key)
                if match:
                    score_odds[(int(match.group(1)), int(match.group(2)))] = price
                elif key in ("s1sh", "s1sd", "s1sa"):
                    other_odds[key] = price
            if len(score_odds) < 20:
                continue
            inverse = {k: 1.0 / v for k, v in score_odds.items()}
            total = sum(inverse.values()) + sum(1.0 / v for v in other_odds.values())
            scores = {k: v / total for k, v in inverse.items()}

            favourite = 0 if one_x_two[0] > one_x_two[2] else 2
            winner_label = ("主胜", "平局", "客胜")[favourite]
            # margins conditional on the picked side winning
            wins = {margin: 0.0 for margin in (1, 2, 3, 4)}
            for (home, away), probability in scores.items():
                margin = home - away if favourite == 0 else away - home
                if margin <= 0:
                    continue
                bucket = min(margin, 4)
                wins[bucket] += probability
            win_total = sum(wins.values())
            conditional = {k: (v / win_total if win_total else 0.0) for k, v in wins.items()}

            # which handicap outcome does each margin produce on this line?
            def handicap_outcome(margin: int, side: int) -> str:
                adjusted = (margin if side == 0 else -margin) + line
                return "让胜" if adjusted > 0 else ("让平" if adjusted == 0 else "让负")

            mapping = {k: handicap_outcome(k, favourite) for k in (1, 2, 3, 4)}
            rows.append({
                "num": item.get("matchNumStr"), "league": league,
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "line": line, "p": [round(x, 4) for x in one_x_two],
                "winner": winner_label,
                "winner_probability": round(max(one_x_two[0], one_x_two[2]), 4),
                "conditional": {f"赢{k}球" if k < 4 else "赢4+球": round(v, 4)
                                for k, v in conditional.items()},
                "mapping": mapping,
                "top_margin": max(conditional.items(), key=lambda kv: kv[1])[0],
            })

    rows.sort(key=lambda r: -r["winner_probability"])
    json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "matches": rows}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(rows)} 场\n")
    for row in rows:
        p = row["p"]
        log(f"{row['num']} {row['league']} {row['home']}-{row['away']}  让{row['line']:+.0f}")
        log(f"  ① 谁赢: 主 {100 * p[0]:.1f}%  平 {100 * p[1]:.1f}%  客 {100 * p[2]:.1f}%"
            f"  → {row['winner']} {100 * row['winner_probability']:.1f}%")
        pieces = []
        for margin in (1, 2, 3, 4):
            key = f"赢{margin}球" if margin < 4 else "赢4+球"
            value = row["conditional"][key]
            pieces.append(f"{key} {100 * value:.1f}%({row['mapping'][margin]})")
        log(f"  ② 若{row['winner']}: " + "  ".join(pieces))
        log(f"  → 最常见: 赢{row['top_margin']}球"
            f"  ({100 * row['conditional'][f'赢{row['top_margin']}球' if row['top_margin'] < 4 else '赢4+球']:.1f}%)"
            f"  对应 {row['mapping'][row['top_margin']]}")
        log("")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
