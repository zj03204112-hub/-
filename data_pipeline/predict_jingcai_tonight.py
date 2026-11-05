#!/usr/bin/env python3
"""Produce tonight's jingcai forecast from the official odds, not from our features.

This is deliberately market-derived. Every experiment in this repository showed the
feature models trail the market, so an honest forecast starts from the prices and only
adds the score-distribution machinery on top:

  1. de-vig the official 1X2 prices           -> P(home) / P(draw) / P(away)
  2. de-vig the official total-goals prices   -> shape of the total-goal distribution,
     which pins down lambda_total without needing over/under lines
  3. split lambda_total into home and away lambdas so the independent-Poisson matrix
     reproduces the 1X2 probabilities as closely as possible (V5.2 machinery)
  4. read the two most likely scorelines off the matrix, and map the official handicap
     line through the same matrix for the 让球胜平负 three-way

The output is a market forecast expressed as scores and handicap probabilities. It is not
an independent edge; it exists so the numbers can be inspected and tracked.

Usage:
    python data_pipeline/predict_jingcai_tonight.py
    python data_pipeline/predict_jingcai_tonight.py --league 欧冠
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "data_pipeline"))
sys.path.insert(0, REPO_ROOT)

API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
POOLS = "had,hhad,ttg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 Mobile/15E148",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}


def fetch() -> dict:
    url = API + "?" + urllib.parse.urlencode({"poolCode": POOLS, "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def devig(odds: list[float]) -> list[float]:
    inverse = [1.0 / o for o in odds]
    total = sum(inverse)
    return [x / total for x in inverse]


def poisson(lam: float, n: int = 13) -> list[float]:
    values = [math.exp(-lam)]
    for k in range(1, n):
        values.append(values[-1] * lam / k)
    return values


def total_from_ttg(ttg: dict) -> float | None:
    """Fit lambda_total so a Poisson reproduces the de-vigged total-goals buckets."""
    keys = [f"s{k}" for k in range(8)]
    odds = []
    for key in keys:
        try:
            odds.append(float(ttg.get(key)))
        except (TypeError, ValueError):
            return None
    probabilities = devig(odds)
    # bucket 7 means "7 or more"
    best_lam, best_error = None, float("inf")
    lam = 0.4
    while lam <= 6.0:
        pmf = poisson(lam, 20)
        buckets = [pmf[k] for k in range(7)] + [sum(pmf[7:])]
        error = sum((buckets[i] - probabilities[i]) ** 2 for i in range(8))
        if error < best_error:
            best_error, best_lam = error, lam
        lam += 0.01
    return best_lam


def allocate(total: float, p_home: float, p_draw: float, p_away: float,
             steps: int = 401, max_goals: int = 13) -> tuple[float, float]:
    best = (float("inf"), total / 2, total / 2)
    for index in range(steps):
        lh = total * index / (steps - 1)
        la = total - lh
        hp, ap = poisson(lh, max_goals), poisson(la, max_goals)
        home = draw = away = 0.0
        for i in range(max_goals):
            for j in range(max_goals):
                p = hp[i] * ap[j]
                if i > j:
                    home += p
                elif i == j:
                    draw += p
                else:
                    away += p
        error = (home - p_home) ** 2 + (draw - p_draw) ** 2 + (away - p_away) ** 2
        if error < best[0]:
            best = (error, lh, la)
    return best[1], best[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="欧冠")
    parser.add_argument("--all", action="store_true", help="ignore the league filter")
    args = parser.parse_args()

    payload = fetch()
    groups = (payload.get("value") or {}).get("matchInfoList") or []
    rows = []
    for group in groups:
        for item in group.get("subMatchList") or []:
            if not args.all and args.league not in (item.get("leagueAbbName") or ""):
                continue
            had, hhad, ttg = item.get("had") or {}, item.get("hhad") or {}, item.get("ttg") or {}
            try:
                odds = [float(had["h"]), float(had["d"]), float(had["a"])]
            except (KeyError, TypeError, ValueError):
                continue
            if any(o <= 1 for o in odds):
                continue
            p_home, p_draw, p_away = devig(odds)
            total = total_from_ttg(ttg)
            if not total:
                continue
            lh, la = allocate(total, p_home, p_draw, p_away)

            hp, ap = poisson(lh), poisson(la)
            scores = sorted(((hp[i] * ap[j], i, j) for i in range(13) for j in range(13)),
                            reverse=True)
            try:
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (TypeError, ValueError):
                line = None
            three = None
            if line is not None and abs(line - round(line)) < 1e-9:
                win = draw = lose = 0.0
                for probability, i, j in scores:
                    margin = i - j + line
                    if margin > 0:
                        win += probability
                    elif margin == 0:
                        draw += probability
                    else:
                        lose += probability
                mass = win + draw + lose
                market_three = None
                try:
                    hh = devig([float(hhad["h"]), float(hhad["d"]), float(hhad["a"])])
                    market_three = hh
                except (KeyError, TypeError, ValueError):
                    pass
                three = {"line": line, "model": [win / mass, draw / mass, lose / mass],
                         "market": market_three}

            kickoff = None
            try:
                local = datetime.fromisoformat(f"{item.get('matchDate')}T{item.get('matchTime')}")
                kickoff = (local - timedelta(hours=8)).strftime("%m-%d %H:%M UTC")
            except ValueError:
                kickoff = "?"

            # confidence from the gap between the top two handicap probabilities:
            # measured on 5726 archived matches, gap <0.06 hits 34-45%, gap >=0.15 hits 55.5%
            gap = None
            confidence = None
            if three:
                ordered = sorted(three["market"] or three["model"], reverse=True)
                gap = ordered[0] - ordered[1]
                confidence = "high" if gap >= 0.15 else ("low" if gap < 0.06 else "mid")
            deep_line = None
            if line is not None:
                deep_line = abs(line) >= 2

            rows.append({
                "num": item.get("matchNumStr"), "league": item.get("leagueAbbName"),
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": kickoff, "odds": odds,
                "p": [p_home, p_draw, p_away], "lambda": [lh, la], "total": total,
                "top": [(round(p, 4), f"{i}-{j}") for p, i, j in scores[:2]],
                "three": three, "gap": gap, "confidence": confidence,
                "deep_line": deep_line,
            })

    if not rows:
        print(f"no on-sale matches for league filter {args.league!r}")
        return 1

    print(f"{'编号':8s} {'开球':14s} {'对阵':28s} {'主胜 平 客胜':>18s} {'λ主/客':>12s} "
          f"{'比分1':>6s} {'比分2':>6s} {'置信':>5s}")
    for row in rows:
        hh, dd, aa = row["p"]
        print(f"{row['num']:8s} {row['kickoff']:14s} "
              f"{(row['home'] + '-' + row['away'])[:28]:30s} "
              f"{hh:6.3f} {dd:6.3f} {aa:6.3f} "
              f"{row['lambda'][0]:5.2f}/{row['lambda'][1]:5.2f} "
              f"{row['top'][0][1]:>6s} {row['top'][1][1]:>6s} "
              f"{(row['confidence'] or '-'):>5s}")

    print("\n让球胜平负（模型 vs 市场）")
    for row in rows:
        three = row["three"]
        if not three:
            continue
        model = three["model"]
        market = three["market"]
        line = three["line"]
        model_text = " ".join(f"{x:.3f}" for x in model)
        market_text = (" ".join(f"{x:.3f}" for x in market) if market else "n/a")
        flag = "深盘" if row["deep_line"] else ""
        gap_text = f"{row['gap']:.3f}" if row["gap"] is not None else "-"
        print(f"  {row['num']} {row['home'][:6]}-{row['away'][:6]} 让{line:+.0f}  "
              f"模型 {model_text}  市场 {market_text}  领先 {gap_text} {row['confidence']} {flag}")

    out = os.path.join(REPO_ROOT, "model_validation",
                       f"jingcai_forecast_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json")
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nreport: {os.path.relpath(out, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
