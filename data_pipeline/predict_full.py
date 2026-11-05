#!/usr/bin/env python3
"""Full prediction per match: result, handicap, margin, scoreline, plus absences.

Everything comes from the official pools and the daily injury snapshot, so the output is
internally consistent:

  had   -> 主胜 / 平 / 客胜
  crs   -> full scoreline probabilities, hence the margin distribution
  margin distribution + the official line -> 让胜 / 让平 / 让负, derived coherently
  hhad  -> the market's own three-way, shown next to the derived one for comparison
  hafu  -> top half-time/full-time combination
  injury_team_day -> share of squad transfer value missing

The derived handicap is the honest one to act on: it is computed by summing the scoreline
probabilities that fall on each side of the line, which is exactly how the bet settles.

Usage:
    python data_pipeline/predict_full.py
    python data_pipeline/predict_full.py --league 欧罗巴
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "full_prediction.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
SCORE_KEY = re.compile(r"^s(\d\d)s(\d\d)$")
HAFU_LABELS = {"hh": "胜胜", "hd": "胜平", "ha": "胜负", "dh": "平胜", "dd": "平平",
               "da": "平负", "ah": "负胜", "ad": "负平", "aa": "负负"}

# Chinese name -> FotMob team name, for the teams our injury snapshot covers
TEAM_MAP = {
    "水晶宫": "Crystal Palace", "霍芬海姆": "Hoffenheim", "伯恩茅斯": "Bournemouth",
    "奈梅亨": "NEC Nijmegen", "皇家社会": "Sociedad", "里昂": "Lyon", "AC米兰": "Milan",
    "弗赖堡": "Freiburg", "桑德兰": "Sunderland", "塞尔塔": "Celta", "阿尔克马": "AZ Alkmaar",
    "尤文图斯": "Juventus", "勒沃库森": "Leverkusen", "马赛": "Marseille",
    "多特蒙德": "Dortmund", "拜仁": "Bayern Munich", "莱红牛": "RB Leipzig",
    "汉堡": "Hamburger SV", "门兴": "Borussia Mönchengladbach", "威尼斯": "Venezia",
    "乌迪内斯": "Udinese", "塞维利亚": "Sevilla", "阿拉维斯": "Alaves", "埃弗顿": "Everton",
    "考文垂": "Coventry", "图卢兹": "Toulouse", "斯特拉斯": "Strasbourg",
    "蒙彼利埃": "Montpellier", "梅斯": "Metz", "法马利康": "Famalicao", "阿罗卡": "Arouca",
}


def log(message: str) -> None:
    print(message, flush=True)


def devig(odds: list[float]) -> list[float]:
    inverse = [1.0 / o for o in odds]
    total = sum(inverse)
    return [x / total for x in inverse]


def margins(scores: dict[tuple[int, int], float], line: float) -> dict[str, float]:
    result = {"让胜": 0.0, "让平": 0.0, "让负": 0.0}
    for (home, away), probability in scores.items():
        adjusted = home - away + line
        if adjusted > 0:
            result["让胜"] += probability
        elif adjusted == 0:
            result["让平"] += probability
        else:
            result["让负"] += probability
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="")
    args = parser.parse_args()

    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    injuries = {}
    for row in connection.execute("""
            SELECT team_name, out_count, injury_count, suspend_count, out_value, squad_value
            FROM injury_team_day
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM injury_team_day)"""):
        share = (row["out_value"] / row["squad_value"]) if row["squad_value"] else None
        injuries[row["team_name"]] = {
            "out": row["out_count"], "injuries": row["injury_count"],
            "suspensions": row["suspend_count"],
            "out_value_m": round((row["out_value"] or 0) / 1e6, 1),
            "share": round(share, 4) if share is not None else None}

    url = API + "?" + urllib.parse.urlencode(
        {"poolCode": "had,hhad,crs,hafu", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    rows = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            league = item.get("leagueAbbName") or ""
            if args.league and args.league not in league:
                continue
            had, hhad, crs, hafu = (item.get("had") or {}, item.get("hhad") or {},
                                    item.get("crs") or {}, item.get("hafu") or {})
            try:
                one_x_two = [float(had["h"]), float(had["d"]), float(had["a"])]
                three = [float(hhad["h"]), float(hhad["d"]), float(hhad["a"])]
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (KeyError, TypeError, ValueError):
                continue
            if any(o <= 1.0 for o in one_x_two + three):
                continue

            score_odds = {}
            other_odds = {}
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
            scores = {}
            if len(score_odds) >= 20:
                inverse = {k: 1.0 / v for k, v in score_odds.items()}
                total = sum(inverse.values()) + sum(1.0 / v for v in other_odds.values())
                scores = {k: v / total for k, v in inverse.items()}
                # spread the open-ended buckets over the listed margins of their category so
                # big wins are not silently dropped from the handicap derivation
                if other_odds:
                    home_mass = sum(v for (h, a), v in scores.items() if h - a >= 1)
                    away_mass = sum(v for (h, a), v in scores.items() if h - a <= -1)
                    draw_probability = 1.0 / other_odds["s1sd"] / total if "s1sd" in other_odds else 0.0
                    scores[(0, 0)] = scores.get((0, 0), 0.0) + draw_probability
                    if home_mass > 0 and "s1sh" in other_odds:
                        extra = 1.0 / other_odds["s1sh"] / total
                        for key, value in list(scores.items()):
                            h, a = key
                            if h - a >= 1:
                                scores[key] = value + extra * value / home_mass
                    if away_mass > 0 and "s1sa" in other_odds:
                        extra = 1.0 / other_odds["s1sa"] / total
                        for key, value in list(scores.items()):
                            h, a = key
                            if h - a <= -1:
                                scores[key] = value + extra * value / away_mass

            p = devig(one_x_two)
            market_three = devig(three)
            derived = margins(scores, line) if scores else None

            hafu_probs = {}
            hafu_odds = {}
            for key in HAFU_LABELS:
                try:
                    value = float(hafu.get(key))
                except (TypeError, ValueError):
                    continue
                if value > 1.0:
                    hafu_odds[key] = value
            if len(hafu_odds) == 9:
                inverse = {k: 1.0 / v for k, v in hafu_odds.items()}
                total = sum(inverse.values())
                hafu_probs = {HAFU_LABELS[k]: round(v / total, 4) for k, v in inverse.items()}

            top_scores = sorted(((v, f"{h}-{a}") for (h, a), v in scores.items()), reverse=True)[:3]
            buckets = {}
            if scores:
                for (h, a), v in scores.items():
                    diff = h - a
                    if diff >= 4:
                        name = "主赢4+"
                    elif diff == 3:
                        name = "主赢3"
                    elif diff == 2:
                        name = "主赢2"
                    elif diff == 1:
                        name = "主赢1"
                    elif diff == 0:
                        name = "平局"
                    elif diff == -1:
                        name = "客赢1"
                    elif diff == -2:
                        name = "客赢2"
                    elif diff == -3:
                        name = "客赢3"
                    else:
                        name = "客赢4+"
                    buckets[name] = buckets.get(name, 0.0) + v
            margin_top = sorted(buckets.items(), key=lambda kv: -kv[1])[:3]

            home_injury = injuries.get(TEAM_MAP.get(item.get("homeTeamAbbName") or "", ""))
            away_injury = injuries.get(TEAM_MAP.get(item.get("awayTeamAbbName") or "", ""))

            best_three = max(range(3), key=lambda i: derived[list(derived)[i]]) if derived else None
            rows.append({
                "num": item.get("matchNumStr"), "league": league,
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "p": [round(x, 4) for x in p],
                "p_home_win": round(sum(v for (h, a), v in scores.items() if h > a), 4) if scores else None,
                "p_draw": round(sum(v for (h, a), v in scores.items() if h == a), 4) if scores else None,
                "p_away_win": round(sum(v for (h, a), v in scores.items() if h < a), 4) if scores else None,
                "line": line,
                "three_market": [round(x, 4) for x in market_three],
                "three_derived": ({k: round(v, 4) for k, v in derived.items()} if derived else None),
                "margin_top": [(name, round(v, 4)) for name, v in margin_top],
                "scores_top": [(score, round(v, 4)) for v, score in top_scores],
                "hafu_top": sorted(hafu_probs.items(), key=lambda kv: -kv[1])[:2] if hafu_probs else [],
                "injury_home": home_injury, "injury_away": away_injury,
            })

    rows.sort(key=lambda r: -max(r["p"][0], r["p"][2]))
    json.dump({"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "matches": rows}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"在售 {len(rows)} 场\n")
    for row in rows:
        p = row["p"]
        log(f"{row['num']} {row['league']} {row['home']}-{row['away']}  让{row['line']:+.0f}")
        log(f"  胜平负  主 {100 * p[0]:.1f}%  平 {100 * p[1]:.1f}%  客 {100 * p[2]:.1f}%")
        if row["three_derived"]:
            d = row["three_derived"]
            m = dict(zip(("让胜", "让平", "让负"), row["three_market"]))
            log(f"  让球(比分类推) 让胜 {100 * d['让胜']:.1f}%  让平 {100 * d['让平']:.1f}%  "
                f"让负 {100 * d['让负']:.1f}%   ← 市场 {100 * m['让胜']:.1f}/"
                f"{100 * m['让平']:.1f}/{100 * m['让负']:.1f}")
        if row["scores_top"]:
            log("  比分  " + "  ".join(f"{s} {100 * v:.1f}%" for s, v in row["scores_top"]))
        if row["margin_top"]:
            log("  净胜球  " + "  ".join(f"{n} {100 * v:.1f}%" for n, v in row["margin_top"]))
        if row["hafu_top"]:
            log("  半全场  " + "  ".join(f"{n} {100 * v:.1f}%" for n, v in row["hafu_top"]))
        for side in ("injury_home", "injury_away"):
            info = row[side]
            if info:
                share = f"{100 * info['share']:.1f}%" if info["share"] is not None else "?"
                log(f"  {side == 'injury_home' and '主队' or '客队'}伤停 缺 {info['out']} 人，"
                    f"占身价 {share}")
        log("")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
