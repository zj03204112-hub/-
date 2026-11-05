#!/usr/bin/env python3
"""Compare the model's handicap view with the jingcai handicap market.

The product is the jingcai three-way handicap (让球胜平负), and this keeps that scope: for
each archived jingcai match we locate the same fixture in the legacy database, run the
shipped V4 model to get its two lambdas, build the score matrix, and read the handicap
three-way off that matrix with the official handicap line. The market side is the de-vigged
three-way price stored in jc_history.

Name matching is unavoidable because the archive uses Chinese club names: Chinese names map
to one or more legacy names (the database contains duplicates such as Inter / Inter Milan),
then the fixture is matched by league, date (one day tolerance) and both team names.

Reports accuracy, Brier and log loss for the model and the market, plus the realised return
of a flat stake on each side's top pick at the archived prices.

Usage:
    python data_pipeline/eval_model_vs_jc_handicap.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(REPO_ROOT, "data_pipeline")
sys.path.insert(0, PIPELINE)

import compare_models as cm                  # noqa: E402

DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "model_vs_jc_handicap.json")

# Chinese name -> acceptable legacy names (the database carries duplicates)
ALIASES: dict[str, tuple[str, ...]] = {
    # Premier League
    "伊普斯": ("Ipswich", "Ipswich Town"), "伯恩利": ("Burnley",), "伯恩茅斯": ("Bournemouth",),
    "切尔西": ("Chelsea",), "利兹联": ("Leeds",), "利物浦": ("Liverpool",),
    "南安普敦": ("Southampton",), "埃弗顿": ("Everton",), "富勒姆": ("Fulham",),
    "布伦特": ("Brentford",), "布赖顿": ("Brighton",), "曼城": ("Man City",),
    "曼联": ("Man United",), "桑德兰": ("Sunderland",), "水晶宫": ("Crystal Palace",),
    "热刺": ("Tottenham",), "狼队": ("Wolves",), "纽卡斯尔": ("Newcastle",),
    "维拉": ("Aston Villa",), "考文垂": ("Coventry",), "莱切斯特": ("Leicester",),
    "西汉姆联": ("West Ham", "West Ham United"), "诺丁汉": ("Nott'm Forest",),
    "赫尔城": ("Hull", "Hull City"), "阿森纳": ("Arsenal",),
    # La Liga
    "埃尔切": ("Elche",), "塞尔塔": ("Celta",), "塞维利亚": ("Sevilla",),
    "奥维耶多": ("Oviedo",), "奥萨苏纳": ("Osasuna",), "巴伦西亚": ("Valencia",),
    "巴列卡诺": ("Vallecano",), "巴利亚多": ("Valladolid",), "巴萨": ("Barcelona",),
    "拉帕马斯": ("Las Palmas",), "拉科": ("La Coruna",), "比利亚雷": ("Villarreal",),
    "毕尔巴鄂": ("Ath Bilbao",), "皇家社会": ("Real Sociedad", "Sociedad"),
    "皇马": ("Real Madrid",), "莱万特": ("Levante",), "莱加内斯": ("Leganes",),
    "西班牙人": ("Espanol",), "贝蒂斯": ("Betis", "Real Betis"),
    "赫塔费": ("Getafe",), "赫罗纳": ("Girona",), "阿拉维斯": ("Alaves",),
    "马拉加": ("Malaga",), "马洛卡": ("Mallorca",), "马竞": ("Ath Madrid",),
    # Bundesliga
    "不来梅": ("Werder Bremen", "云达不莱梅"), "勒沃库森": ("Leverkusen", "Bayer Leverkusen"),
    "圣保利": ("St Pauli",), "埃沃斯堡": ("Elversberg", "SV Elversberg"),
    "基尔": ("Holstein Kiel",), "多特蒙德": ("Dortmund", "Borussia Dortmund"),
    "奥格斯堡": ("Augsburg",), "帕德博恩": ("Paderborn",), "弗赖堡": ("Freiburg", "SC Freiburg"),
    "拜仁": ("Bayern Munich",), "斯图加特": ("Stuttgart", "VfB Stuttgart"),
    "柏林联合": ("Union Berlin",), "汉堡": ("Hamburg", "Hamburger SV"),
    "沃夫斯堡": ("Wolfsburg", "沃尔夫斯堡"), "沙尔克04": ("Schalke 04",),
    "法兰克福": ("Eintracht Frankfurt", "Ein Frankfurt"), "波鸿": ("Bochum",),
    "海登海姆": ("Heidenheim",), "科隆": ("FC Koln", "FC Cologne"),
    "美因茨": ("Mainz", "Mainz 05"), "莱红牛": ("RB Leipzig",),
    "门兴": ("M'gladbach", "Borussia Mönchengladbach"), "霍芬海姆": ("Hoffenheim", "TSG Hoffenheim"),
    # Serie A
    "AC米兰": ("AC Milan", "Milan"), "乌迪内斯": ("Udinese",), "亚特兰大": ("Atalanta",),
    "佛罗伦萨": ("Fiorentina",), "克雷莫纳": ("Cremonese",), "博洛尼亚": ("Bologna",),
    "卡利亚里": ("Cagliari",), "国际米兰": ("Inter", "Inter Milan"), "威尼斯": ("Venezia",),
    "尤文图斯": ("Juventus",), "帕尔马": ("Parma",), "弗洛西诺": ("Frosinone",),
    "恩波利": ("Empoli",), "拉齐奥": ("Lazio",), "比萨": ("Pisa",), "热那亚": ("Genoa",),
    "科莫": ("Como",), "维罗纳": ("Verona",), "罗马": ("Roma",), "莱切": ("Lecce",),
    "萨索洛": ("Sassuolo",), "蒙扎": ("Monza",), "那不勒斯": ("Napoli",), "都灵": ("Torino",),
    # Ligue 1
    "兰斯": ("Reims",), "勒阿弗尔": ("Le Havre",), "南特": ("Nantes",), "图卢兹": ("Toulouse",),
    "圣埃蒂安": ("St Etienne",), "尼斯": ("Nice",), "巴黎FC": ("Paris FC",),
    "巴黎圣曼": ("Paris SG",), "布雷斯特": ("Brest",), "摩纳哥": ("Monaco",),
    "斯特拉斯": ("Strasbourg",), "昂热": ("Angers",), "朗斯": ("Lens",), "梅斯": ("Metz",),
    "欧塞尔": ("Auxerre",), "洛里昂": ("Lorient",), "蒙彼利埃": ("Montpellier",),
    "里尔": ("Lille",), "里昂": ("Lyon",), "雷恩": ("Rennes",), "马赛": ("Marseille",),
}

LEAGUE_MAP = {"英超": "EPL", "西甲": "LALIGA", "德甲": "BUNDESLIGA", "意甲": "SERIEA", "法甲": "LIGUE1"}


def log(message: str) -> None:
    print(message, flush=True)


def devig(values: list[float]) -> list[float] | None:
    if any(not math.isfinite(v) or v <= 1.0 for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def handicap_probs(lh: float, la: float, line: float, goals: int = 10) -> list[float]:
    """Three-way handicap probabilities from the model's independent Poisson matrix."""
    hp = [math.exp(-lh)]
    ap = [math.exp(-la)]
    for k in range(1, goals + 1):
        hp.append(hp[-1] * lh / k)
        ap.append(ap[-1] * la / k)
    win = draw = lose = 0.0
    for i in range(goals + 1):
        for j in range(goals + 1):
            probability = hp[i] * ap[j]
            margin = i - j + line
            if margin > 0:
                win += probability
            elif margin == 0:
                draw += probability
            else:
                lose += probability
    total = win + draw + lose
    return [win / total, draw / total, lose / total]


def metrics(rows: list[dict], key: str) -> dict:
    if not rows:
        return {"n": 0}
    n = len(rows)
    correct = sum(1 for r in rows if r[key]["pick"] == r["actual"])
    brier = sum(r[key]["brier"] for r in rows) / n
    logloss = sum(r[key]["logloss"] for r in rows) / n
    stake = sum(r[key]["return"] for r in rows)
    return {"n": n, "accuracy": round(correct / n, 4), "brier": round(brier, 4),
            "logloss": round(logloss, 4), "roi": round(stake / n, 4)}


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row

    legacy: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for row in connection.execute("""
            SELECT m.match_id, m.kickoff, m.home_team, m.away_team, r.ft_home, r.ft_away,
                   c.competition_code
            FROM matches m
            JOIN results r ON r.match_id = m.match_id
            JOIN competitions c ON c.competition_id = m.competition_id
            WHERE m.status = 'finished' AND r.ft_home IS NOT NULL"""):
        legacy[(row[6], row[1][:10])].append(
            (row[0], row[1], row[2], row[3], row[4], row[5]))

    candidates = [dict(r) for r in connection.execute("""
        SELECT matchnum, match_date, league, home, away, home_score, away_score,
               handicap_line, hhad_h, hhad_d, hhad_a, result_hhad, win, draw, lost, result_spf
        FROM jc_history
        WHERE substr(match_date, 1, 4) IN ('2025', '2026')
          AND hhad_h IS NOT NULL AND home_score >= 0 AND away_score >= 0
          AND result_hhad IN ('胜', '平', '负')""")]
    log(f"jingcai candidates: {len(candidates)}")

    matched = []
    unmatched_names = set()
    for row in candidates:
        code = LEAGUE_MAP.get(row["league"])
        if not code:
            continue
        home_alias = ALIASES.get(row["home"])
        away_alias = ALIASES.get(row["away"])
        if not home_alias or not away_alias:
            unmatched_names.add(row["home"] if not home_alias else row["away"])
            continue
        target = (row["home_score"], row["away_score"])
        found = None
        for delta in (0, 1, -1):
            day = (date.fromisoformat(row["match_date"]) + timedelta(days=delta)).isoformat()
            for fixture in legacy.get((code, day), []):
                if (fixture[2] in home_alias and fixture[3] in away_alias
                        and (fixture[4], fixture[5]) == target):
                    found = fixture
                    break
            if found:
                break
        if found:
            matched.append((row, found))
    log(f"matched fixtures: {len(matched)}; unmapped names: {sorted(unmatched_names)}")
    if not matched:
        return 1

    rows = []
    for jc_row, fixture in matched:
        lambdas = cm.model_lambdas(connection, "v4", tuple(fixture))
        line = jc_row["handicap_line"]
        model = handicap_probs(lambdas[0], lambdas[1], line)
        market = devig([jc_row["hhad_h"], jc_row["hhad_d"], jc_row["hhad_a"]])
        if not market:
            continue
        labels = ["胜", "平", "负"]
        actual = labels.index(jc_row["result_hhad"])
        odds = [jc_row["hhad_h"], jc_row["hhad_d"], jc_row["hhad_a"]]

        record = {"actual": actual, "line": line}
        for name, probabilities in (("model", model), ("market", market)):
            pick = max(range(3), key=lambda i: probabilities[i])
            y = [1.0 if i == actual else 0.0 for i in range(3)]
            record[name] = {
                "pick": pick,
                "brier": sum((probabilities[i] - y[i]) ** 2 for i in range(3)),
                "logloss": -math.log(max(1e-12, probabilities[actual])),
                "return": (odds[pick] - 1.0) if pick == actual else -1.0,
                "probability": probabilities,
            }
        record["same_pick"] = record["model"]["pick"] == record["market"]["pick"]

        # plain 1X2 on the same fixtures
        model_1x2, _ = cm.run_model(connection, "v4", tuple(fixture))
        market_1x2 = devig([jc_row["win"], jc_row["draw"], jc_row["lost"]])
        if market_1x2 and jc_row["result_spf"] in labels:
            actual_1x2 = labels.index(jc_row["result_spf"])
            plain = {"actual": actual_1x2}
            for name, probabilities in (("model", list(model_1x2.values())),
                                        ("market", market_1x2)):
                pick = max(range(3), key=lambda i: probabilities[i])
                y = [1.0 if i == actual_1x2 else 0.0 for i in range(3)]
                plain[name] = {
                    "pick": pick,
                    "brier": sum((probabilities[i] - y[i]) ** 2 for i in range(3)),
                    "logloss": -math.log(max(1e-12, probabilities[actual_1x2])),
                    "return": 0.0,
                }
            record["plain"] = plain
        rows.append(record)

    summary = {
        "definition": (
            "V4 model versus the archived jingcai handicap market, 2025-2026, five European "
            "leagues. Model probabilities come from the shipped V4 lambdas through an "
            "independent Poisson matrix using the official handicap line. Market side is the "
            "de-vigged three-way price. ROI is a flat stake on each side's own top pick."),
        "matched": len(rows),
        "model": metrics(rows, "model"),
        "market": metrics(rows, "market"),
        "same_pick_share": round(sum(1 for r in rows if r["same_pick"]) / len(rows), 4),
        "model_return_sum": round(sum(r["model"]["return"] for r in rows), 2),
        "market_return_sum": round(sum(r["market"]["return"] for r in rows), 2),
        "by_line": {},
    }
    for line in sorted({r["line"] for r in rows}):
        subset = [r for r in rows if r["line"] == line]
        if len(subset) >= 30:
            summary["by_line"][f"{line:+.0f}"] = {
                "n": len(subset), "model": metrics(subset, "model"),
                "market": metrics(subset, "market")}

    json.dump(summary, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    plain_rows = [r["plain"] for r in rows if "plain" in r]
    summary["plain_1x2"] = {
        "n": len(plain_rows),
        "model": {k: v for k, v in metrics(plain_rows, "model").items() if k != "roi"},
        "market": {k: v for k, v in metrics(plain_rows, "market").items() if k != "roi"},
    }
    json.dump(summary, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    for block, label in (("plain_1x2", "1X2"), (None, "让球三选一")):
        source = summary[block] if block else summary
        log(f"  {label}:")
        for name in ("model", "market"):
            m = source[name]
            log(f"    {name:7s} n={m['n']:4d} acc={m['accuracy']:.3f} brier={m['brier']:.4f} "
                f"logloss={m['logloss']:.4f}" + (f" roi={m['roi']:+.4f}" if "roi" in m else ""))
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
