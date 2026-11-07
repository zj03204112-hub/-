#!/usr/bin/env python3
"""The card in the form asked for: 不败 as the pick, the three prices, and the risk reasons.

No double-chance probability is printed, because the pick is already the double chance; what
the reader needs instead is why it might lose. Those reasons are derived from stored data
rather than invented:

  走势   how the handicap price moved since the opening snapshot, and whether it moved against
         the pick
  近况   each side's last five results
  交锋   the head-to-head record in the archive
  深盘   the line itself, which decides how hard the pick is to land

The three 1X2 probabilities are still printed, with the draw made explicit, because the draw is
now part of the bet and should be read as one of the outcomes rather than ignored.

Usage:
    python data_pipeline/risk_card.py --prefix 周六
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from devig import power_devig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "risk_card.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
RESULT_INDEX = {"胜": 0, "平": 1, "负": 2}


def log(message: str) -> None:
    print(message, flush=True)


def team_history() -> tuple[dict[str, list[int]], dict[tuple[str, str], list[int]]]:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    form: dict[str, list[int]] = defaultdict(list)
    h2h: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in connection.execute("""
            SELECT match_date, home, away, result_spf FROM jc_history
            WHERE result_spf IN ('胜','平','负') AND home_score >= 0
            ORDER BY match_date"""):
        home, away = row["home"], row["away"]
        outcome = RESULT_INDEX[row["result_spf"]]
        form[home].append(3 if outcome == 0 else (1 if outcome == 1 else 0))
        form[away].append(3 if outcome == 2 else (1 if outcome == 1 else 0))
        h2h[(home, away)].append(0 if outcome == 0 else (14 if outcome == 1 else 2))
    return form, h2h


def movement(first: dict | None, last: dict | None) -> float | None:
    if not first or not last:
        return None
    p0 = power_devig([first["hhad_h"], first["hhad_d"], first["hhad_a"]])
    p1 = power_devig([last["hhad_h"], last["hhad_d"], last["hhad_a"]])
    if not p0 or not p1:
        return None
    return max(p0[i] - p1[i] for i in range(3)) * (1 if p1[0] >= p0[0] else -1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()

    form, h2h = team_history()
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    snapshots: dict[str, list[dict]] = defaultdict(list)
    for row in connection.execute("""
            SELECT match_num, snapshot_at, hhad_h, hhad_d, hhad_a FROM sporttery_snapshot
            WHERE hhad_h > 1 ORDER BY match_num, snapshot_at"""):
        snapshots[row["match_num"]].append(dict(row))

    url = API + "?" + urllib.parse.urlencode({"poolCode": "had,hhad", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    cards = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            number = item.get("matchNumStr") or ""
            if args.prefix and not number.startswith(args.prefix):
                continue
            had, hhad = item.get("had") or {}, item.get("hhad") or {}
            try:
                p = power_devig([float(had["h"]), float(had["d"]), float(had["a"])])
                three = power_devig([float(hhad["h"]), float(hhad["d"]), float(hhad["a"])])
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (KeyError, TypeError, ValueError):
                continue
            if not p or not three:
                continue
            home = item.get("homeTeamAbbName") or ""
            away = item.get("awayTeamAbbName") or ""
            favourite = 0 if p[0] > p[2] else 2
            side = "主" if favourite == 0 else "客"
            opponent = "客" if favourite == 0 else "主"
            home_form = form.get(home, [])[-5:]
            away_form = form.get(away, [])[-5:]
            meetings = h2h.get((home, away), [])

            reasons = []
            series = snapshots.get(number) or []
            if len(series) >= 2:
                first, last = series[0], series[-1]
                delta_top = None
                try:
                    p0 = power_devig([first["hhad_h"], first["hhad_d"], first["hhad_a"]])
                    p1 = power_devig([last["hhad_h"], last["hhad_d"], last["hhad_a"]])
                    if p0 and p1:
                        delta_top = (p1[0] - p0[0]) * 100
                except (TypeError, ValueError):
                    delta_top = None
                if delta_top is not None:
                    if abs(delta_top) >= 4:
                        direction = "主队" if delta_top > 0 else "客队"
                        reasons.append(f"盘口向{direction}移动 {abs(delta_top):.1f}pp"
                                       f"（{len(series)} 次快照）")

            def form_text(values: list[int]) -> str:
                wins = sum(1 for value in values if value == 3)
                draws = sum(1 for value in values if value == 1)
                losses = len(values) - wins - draws
                return f"{wins}胜{draws}平{losses}负"

            if home_form:
                reasons.append(f"{home}近5场 {form_text(home_form)}")
            if away_form:
                reasons.append(f"{away}近5场 {form_text(away_form)}")
            if meetings:
                home_wins = sum(1 for value in meetings if value == 0)
                draws_h2h = sum(1 for value in meetings if value == 14)
                reasons.append(f"交锋 {len(meetings)} 次：{home} {home_wins}胜{draws_h2h}平"
                               f"{len(meetings) - home_wins - draws_h2h}负")
            if abs(line) >= 2:
                reasons.append(f"深盘 让{line:+.0f}，「{side}不败」需对手不赢两球以上")

            cards.append({
                "num": number, "league": item.get("leagueAbbName"),
                "home": home, "away": away, "line": line,
                "p": [round(x, 4) for x in p],
                "recommendation": f"{side}不败",
                "pick_side": side, "opponent": opponent,
                "handicap_pick": ("让胜", "让平", "让负")[
                    max(range(3), key=lambda i: three[i])],
                "handicap_probability": round(max(three), 4),
                "handicap_gap": round(max(three) - sorted(three)[-2], 4),
                "risk_reasons": reasons,
            })

    cards.sort(key=lambda c: -max(c["p"][0] if c["pick_side"] == "主" else c["p"][2],
                                 c["p"][1]))
    json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "cards": cards}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(cards)} 场\n")
    for card in cards:
        p = card["p"]
        log(f"{card['num']} {card['league']} {card['home']}-{card['away']}  让{card['line']:+.0f}")
        log(f"  推荐  {card['recommendation']}")
        log(f"  概率  主胜 {100 * p[0]:.1f}% / 平局 {100 * p[1]:.1f}% / 客胜 {100 * p[2]:.1f}%")
        if card["risk_reasons"]:
            log("  风险  " + " | ".join(card["risk_reasons"]))
        log(f"  让球  {card['handicap_pick']} {100 * card['handicap_probability']:.1f}%"
            f"  (领先 {100 * card['handicap_gap']:.1f}pp)")
        log("")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
