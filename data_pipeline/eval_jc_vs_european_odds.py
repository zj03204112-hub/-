#!/usr/bin/env python3
"""Validate the jingcai-derived probabilities against European bookmakers.

The question is accuracy, not margin. Proper scoring rules (Brier, log loss) compare a
probability with the realised outcome and do not care what price was offered, so a de-vigged
jingcai line and a de-vigged Bet365 line can be compared directly and fairly.

For every fixture present in both sources the script reports:

  * the three probability triples (jingcai, Bet365, Pinnacle), de-vigged
  * mean absolute difference between them
  * accuracy of the argmax pick
  * multiclass Brier and log loss against the actual result

If the jingcai line is as good as the European prices, the scoring rules will say so.

Usage:
    python data_pipeline/eval_jc_vs_european_odds.py
    python data_pipeline/eval_jc_vs_european_odds.py --leagues 英超 西甲
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sqlite3
import sys
import urllib.request
from datetime import date, datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "jc_vs_european_odds.json")
CACHE = os.path.join(REPO_ROOT, "data", "cache", "football_data")
BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "Chrome/120 Safari/537.36"}

LEAGUE_CODES = {"英超": "E0", "西甲": "SP1", "德甲": "D1", "意甲": "I1", "法甲": "F1"}
SEASONS = ["2223", "2324", "2425", "2526"]

# Chinese club name -> names used by football-data.co.uk
ALIASES: dict[str, tuple[str, ...]] = {
    "伊普斯": ("Ipswich",), "伯恩利": ("Burnley",), "伯恩茅斯": ("Bournemouth",),
    "切尔西": ("Chelsea",), "利兹联": ("Leeds",), "利物浦": ("Liverpool",),
    "南安普敦": ("Southampton", "So'ton"), "埃弗顿": ("Everton",), "富勒姆": ("Fulham",),
    "布伦特": ("Brentford",), "布赖顿": ("Brighton",), "曼城": ("Man City",),
    "曼联": ("Man United",), "桑德兰": ("Sunderland",), "水晶宫": ("Crystal Palace",),
    "热刺": ("Tottenham",), "狼队": ("Wolves",), "纽卡斯尔": ("Newcastle",),
    "维拉": ("Aston Villa",), "考文垂": ("Coventry",), "莱切斯特": ("Leicester",),
    "西汉姆联": ("West Ham",), "诺丁汉": ("Nott'm Forest",), "赫尔城": ("Hull",),
    "阿森纳": ("Arsenal",), "谢菲联": ("Sheffield United",), "卢顿": ("Luton",),
    "西布罗姆": ("West Brom",), "斯托克城": ("Stoke",), "米德尔斯堡": ("Middlesbrough",),
    "斯旺西": ("Swansea",), "卡迪夫城": ("Cardiff",), "沃特福德": ("Watford",),
    "诺维奇": ("Norwich",), "伯明翰": ("Birmingham",), "米尔沃尔": ("Millwall",),
    "普雷斯顿": ("Preston",), "布里斯托": ("Bristol City",), "女王巡游": ("QPR",),
    "罗瑟汉姆": ("Rotherham",), "哈德斯": ("Huddersfield",), "布莱克本": ("Blackburn",),
    "雷丁": ("Reading",), "维冈": ("Wigan",), "布莱克浦": ("Blackpool",),
    "埃尔切": ("Elche",), "塞尔塔": ("Celta",), "塞维利亚": ("Sevilla",),
    "奥维耶多": ("Oviedo",), "奥萨苏纳": ("Osasuna",), "巴伦西亚": ("Valencia",),
    "巴列卡诺": ("Vallecano",), "巴利亚多": ("Valladolid",), "巴萨": ("Barcelona",),
    "拉帕马斯": ("Las Palmas",), "比利亚雷": ("Villarreal",), "毕尔巴鄂": ("Ath Bilbao",),
    "皇家社会": ("Real Sociedad",), "皇马": ("Real Madrid",), "莱万特": ("Levante",),
    "莱加内斯": ("Leganes",), "西班牙人": ("Espanol",), "贝蒂斯": ("Betis",),
    "赫塔费": ("Getafe",), "赫罗纳": ("Girona",), "阿拉维斯": ("Alaves",),
    "马洛卡": ("Mallorca",), "马竞": ("Ath Madrid",), "阿梅里亚": ("Almeria",),
    "加的斯": ("Cadiz",), "格拉纳达": ("Granada",),
    "不来梅": ("Werder Bremen",), "勒沃库森": ("Leverkusen",), "圣保利": ("St Pauli",),
    "基尔": ("Holstein Kiel",), "多特蒙德": ("Dortmund",), "奥格斯堡": ("Augsburg",),
    "弗赖堡": ("Freiburg",), "拜仁": ("Bayern Munich",), "斯图加特": ("Stuttgart",),
    "柏林联合": ("Union Berlin",), "汉堡": ("Hamburg",), "沃夫斯堡": ("Wolfsburg",),
    "法兰克福": ("Ein Frankfurt",), "波鸿": ("Bochum",), "海登海姆": ("Heidenheim",),
    "科隆": ("FC Koln",), "美因茨": ("Mainz",), "莱红牛": ("RB Leipzig",),
    "门兴": ("M'gladbach",), "霍芬海姆": ("Hoffenheim",), "达姆施塔": ("Darmstadt",),
    "AC米兰": ("Milan",), "乌迪内斯": ("Udinese",), "亚特兰大": ("Atalanta",),
    "佛罗伦萨": ("Fiorentina",), "克雷莫纳": ("Cremonese",), "博洛尼亚": ("Bologna",),
    "卡利亚里": ("Cagliari",), "国际米兰": ("Inter",), "威尼斯": ("Venezia",),
    "尤文图斯": ("Juventus",), "帕尔马": ("Parma",), "恩波利": ("Empoli",),
    "拉齐奥": ("Lazio",), "比萨": ("Pisa",), "热那亚": ("Genoa",), "科莫": ("Como",),
    "维罗纳": ("Verona",), "罗马": ("Roma",), "莱切": ("Lecce",), "萨索洛": ("Sassuolo",),
    "蒙扎": ("Monza",), "那不勒斯": ("Napoli",), "都灵": ("Torino",), "弗洛西诺": ("Frosinone",),
    "萨勒尼塔": ("Salernitana",), "斯佩齐亚": ("Spezia",),
    "兰斯": ("Reims",), "勒阿弗尔": ("Le Havre",), "南特": ("Nantes",), "图卢兹": ("Toulouse",),
    "圣埃蒂安": ("St Etienne",), "尼斯": ("Nice",), "巴黎FC": ("Paris FC",),
    "巴黎圣曼": ("Paris SG",), "布雷斯特": ("Brest",), "摩纳哥": ("Monaco",),
    "斯特拉斯": ("Strasbourg",), "昂热": ("Angers",), "朗斯": ("Lens",), "梅斯": ("Metz",),
    "欧塞尔": ("Auxerre",), "洛里昂": ("Lorient",), "蒙彼利埃": ("Montpellier",),
    "里尔": ("Lille",), "里昂": ("Lyon",), "雷恩": ("Rennes",), "马赛": ("Marseille",),
    "克莱蒙": ("Clermont",), "阿雅克肖": ("Ajaccio",), "特鲁瓦": ("Troyes",),
}

RESULT_INDEX = {"胜": 0, "平": 1, "负": 2}


def log(message: str) -> None:
    print(message, flush=True)


def devig(values: list[float]) -> list[float] | None:
    if any(not math.isfinite(v) or v <= 1.0 for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def download(season: str, code: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{season}_{code}.csv")
    if os.path.exists(path) and os.path.getsize(path) > 5000:
        return path
    request = urllib.request.Request(BASE.format(season=season, code=code), headers=HEADERS)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=40) as response:
        body = response.read()
    with open(path, "wb") as handle:
        handle.write(body)
    return path


def parse_date(text: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def load_feed(season: str, code: str) -> dict[tuple[str, str], dict]:
    path = download(season, code)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    feed: dict[tuple[str, str], dict] = {}
    for row in rows:
        day = parse_date(row.get("Date") or "")
        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        if not day or not home or not away:
            continue
        try:
            fthg, ftag = int(row["FTHG"]), int(row["FTAG"])
        except (KeyError, TypeError, ValueError):
            continue
        def prices(prefix: str) -> list[float] | None:
            try:
                values = [float(row[f"{prefix}H"]), float(row[f"{prefix}D"]),
                          float(row[f"{prefix}A"])]
            except (KeyError, TypeError, ValueError):
                return None
            return values if all(v > 1.0 for v in values) else None
        feed[(day.isoformat(), home)] = {
            "away": away, "fthg": fthg, "ftag": ftag,
            "b365": prices("B365"), "ps": prices("PS"), "avg": prices("Avg"),
        }
    return feed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leagues", nargs="*", default=list(LEAGUE_CODES))
    args = parser.parse_args()

    feeds: dict[str, dict[tuple[str, str], dict]] = {}
    for league in args.leagues:
        code = LEAGUE_CODES[league]
        merged: dict[tuple[str, str], dict] = {}
        for season in SEASONS:
            try:
                merged.update(load_feed(season, code))
            except Exception as error:  # noqa: BLE001
                log(f"  {league} {season}: {type(error).__name__}")
        feeds[league] = merged
        log(f"{league}: {len(merged)} fixtures from football-data")

    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    pairs = []
    for row in connection.execute("""
            SELECT league, match_date, home, away, win, draw, lost, result_spf
            FROM jc_history
            WHERE win > 1 AND draw > 1 AND lost > 1 AND result_spf IN ('胜','平','负')"""):
        league = row["league"]
        if league not in feeds:
            continue
        jc = devig([row["win"], row["draw"], row["lost"]])
        if not jc:
            continue
        home_aliases = ALIASES.get(row["home"], ())
        away_aliases = ALIASES.get(row["away"], ())
        if not home_aliases or not away_aliases:
            continue
        day = date.fromisoformat(row["match_date"])
        found = None
        for offset in (-1, 0, 1):
            key_day = (date.fromordinal(day.toordinal() + offset)).isoformat()
            for home_name in home_aliases:
                entry = feeds[league].get((key_day, home_name))
                if entry and entry["away"] in away_aliases:
                    found = entry
                    break
            if found:
                break
        if not found:
            continue
        actual = RESULT_INDEX[row["result_spf"]]
        if (found["fthg"] > found["ftag"]) != (actual == 0) or \
                (found["fthg"] == found["ftag"]) != (actual == 1):
            continue      # the two sources disagree on the result, drop the fixture
        pairs.append({"league": league, "date": row["match_date"],
                      "home": row["home"], "away": row["away"],
                      "jc": jc, "b365": devig(found["b365"]) if found["b365"] else None,
                      "ps": devig(found["ps"]) if found["ps"] else None,
                      "avg": devig(found["avg"]) if found["avg"] else None,
                      "actual": actual})

    log(f"\nmatched fixtures: {len(pairs)}")
    if not pairs:
        return 1

    def score(key: str) -> dict:
        subset = [p for p in pairs if p[key]]
        if not subset:
            return {"n": 0}
        correct = brier = logloss = 0.0
        for p in subset:
            probs = p[key]
            pick = max(range(3), key=lambda i: probs[i])
            correct += int(pick == p["actual"])
            brier += sum((probs[i] - (1.0 if i == p["actual"] else 0.0)) ** 2
                         for i in range(3))
            logloss -= math.log(max(probs[p["actual"]], 1e-12))
        n = len(subset)
        return {"n": n, "accuracy": round(correct / n, 4),
                "brier": round(brier / n, 5), "log_loss": round(logloss / n, 5)}

    results = {key: score(key) for key in ("jc", "b365", "ps", "avg")}
    log(f"\n{'数据源':>12s} {'场次':>6s} {'命中率':>8s} {'Brier':>9s} {'LogLoss':>9s}")
    labels = {"jc": "竞彩(去水)", "b365": "Bet365", "ps": "Pinnacle", "avg": "欧赔均值"}
    for key in ("jc", "b365", "ps", "avg"):
        value = results[key]
        if not value["n"]:
            continue
        log(f"{labels[key]:>12s} {value['n']:6d} {100 * value['accuracy']:7.2f}% "
            f"{value['brier']:9.4f} {value['log_loss']:9.4f}")

    # how far apart are the probability triples?
    diffs = []
    for p in pairs:
        if p["b365"]:
            diffs.append(max(abs(p["jc"][i] - p["b365"][i]) for i in range(3)))
    if diffs:
        diffs.sort()
        log(f"\n竞彩 vs Bet365 概率最大偏差: 平均 {100 * sum(diffs) / len(diffs):.2f}pp, "
            f"中位 {100 * diffs[len(diffs) // 2]:.2f}pp, "
            f"P90 {100 * diffs[int(len(diffs) * 0.9)]:.2f}pp")

    payload = {"definition": (
                    "Jingcai versus European bookmakers on fixtures present in both sources. "
                    "All triples are de-vigged; Brier and log loss are proper scoring rules and "
                    "do not depend on the price offered."),
               "matched": len(pairs), "scores": results,
               "probability_gap_pp": {
                   "mean": round(100 * sum(diffs) / len(diffs), 2) if diffs else None,
                   "median": round(100 * diffs[len(diffs) // 2], 2) if diffs else None},
               "pairs": [{k: v for k, v in p.items() if k != "actual"} | {"actual": p["actual"]}
                         for p in pairs]}
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
