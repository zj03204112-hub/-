#!/usr/bin/env python3
"""Review a stored jingcai forecast against the actual results.

Reads a forecast JSON produced by predict_jingcai_tonight.py, pulls the official results
from the 500.com daily archive (match number, score, handicap line and settled handicap
result) and scores the forecast on three axes:

  1. one-two-X: accuracy, Brier and log loss of the de-vigged probabilities
  2. exact scores: whether scoreline one or two landed
  3. handicap three-way: argmax hit rate for the model, for the market, and for a rule that
     allows the draw to be selected when it is close to the top probability

The draw rule matters because argmax on a three-way almost never selects the draw, yet the
draw outcome happens about a quarter of the time. Any review that only looks at argmax
therefore hides a structural bias.

Usage:
    python data_pipeline/review_jingcai_forecast.py model_validation/jingcai_forecast_20261104_1404.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://www.500.com/static/public/jczq/xml/hisdata/{year}/{mmdd}/match.xml"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/120 Safari/537.36",
    "Referer": "https://trade.500.com/",
    "Accept": "application/xml,text/xml,*/*",
}


def log(message: str) -> None:
    print(message, flush=True)


def fetch_results(years_days: set[tuple[str, str]]) -> dict[str, dict]:
    """matchnum -> result row, fetched per archived day."""
    out: dict[str, dict] = {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for year, mmdd in sorted(years_days):
        url = BASE.format(year=year, mmdd=mmdd)
        try:
            with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
                body = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            log(f"  {year}/{mmdd}: {type(exc).__name__}")
            continue
        if not body.lstrip().startswith(b"<?xml"):
            log(f"  {year}/{mmdd}: anti-bot challenge, skipped")
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue
        for node in root.iter("match"):
            number = node.get("matchnum") or ""
            if not number:
                continue
            out[number] = {
                "home": node.get("homename"), "away": node.get("awayname"),
                "score": node.get("score"), "line": node.get("rangqiu"),
                "result_hhad": node.get("resultspf"), "result_spf": node.get("resultnspf"),
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forecast")
    args = parser.parse_args()

    path = args.forecast if os.path.isabs(args.forecast) else os.path.join(REPO_ROOT, args.forecast)
    forecast = json.load(open(path, encoding="utf-8"))
    if not forecast:
        log("empty forecast")
        return 1

    needed = set()
    for row in forecast:
        kickoff = row.get("kickoff", "")          # "11-04 17:45 UTC"
        if kickoff[:2].isdigit():
            month, day = kickoff[:2], kickoff[3:5]
            # matches kick off late in the UTC day; the archive files by local date
            for extra in (0, 1):
                d = int(day) + extra
                if d > 31:
                    continue
                needed.add(("2026", f"{month}{d:02d}"))
    results = fetch_results(needed)
    log(f"results fetched: {len(results)} matches")

    hits_1x2 = 0
    brier = 0.0
    logloss = 0.0
    scored = 0
    exact1 = exact2 = 0
    hhad_model = hhad_market = hhad_draw_rule = 0
    hhad_scored = 0
    rows_out = []

    for row in forecast:
        actual = results.get(row["num"])
        if not actual or not actual.get("score") or ":" not in actual["score"]:
            rows_out.append({**row, "actual": None})
            continue
        home_goals, away_goals = (int(x) for x in actual["score"].split(":"))
        outcome = "H" if home_goals > away_goals else ("D" if home_goals == away_goals else "A")
        p = row["p"]
        index = {"H": 0, "D": 1, "A": 2}[outcome]
        scored += 1
        hits_1x2 += int(max(range(3), key=lambda i: p[i]) == index)
        brier += sum((p[i] - (1.0 if i == index else 0.0)) ** 2 for i in range(3))
        logloss -= math.log(max(1e-12, p[index]))

        score1 = row["top"][0][1]
        score2 = row["top"][1][1]
        actual_score = actual["score"].replace(":", "-")   # forecasts use 2-1, archive 2:1
        exact1 += int(score1 == actual_score)
        exact2 += int(score2 == actual_score)

        three = row.get("three")
        model_hit = market_hit = draw_hit = None
        if three and actual.get("result_hhad") in ("胜", "平", "负"):
            labels = ["胜", "平", "负"]
            index_h = labels.index(actual["result_hhad"])
            hhad_scored += 1
            model = three["model"]
            market = three.get("market") or model
            model_pick = max(range(3), key=lambda i: model[i])
            market_pick = max(range(3), key=lambda i: market[i])
            model_hit = model_pick == index_h
            market_hit = market_pick == index_h
            hhad_model += int(model_hit)
            hhad_market += int(market_hit)
            # draw-tolerant rule: take the draw when it is within 90% of the top probability
            top = max(model)
            tolerant = 1 if model[1] >= 0.9 * top else model_pick
            draw_hit = tolerant == index_h
            hhad_draw_rule += int(draw_hit)

        rows_out.append({**row, "actual": actual, "outcome": outcome,
                         "exact1": score1 == actual_score, "exact2": score2 == actual_score,
                         "model_hit": model_hit, "market_hit": market_hit,
                         "draw_rule_hit": draw_hit})

    log(f"\n1X2      n={scored}  accuracy={hits_1x2}/{scored}  "
        f"brier={brier / scored:.4f}  logloss={logloss / scored:.4f}")
    log(f"score    scoreline1 exact {exact1}/{scored}   scoreline2 exact {exact2}/{scored}")
    log(f"handicap n={hhad_scored}  model {hhad_model}/{hhad_scored}  "
        f"market {hhad_market}/{hhad_scored}  draw-tolerant rule {hhad_draw_rule}/{hhad_scored}")

    print()
    for item in rows_out:
        actual = item.get("actual")
        if not actual:
            print(f"  {item['num']} {item['home']}-{item['away']}: 无结果")
            continue
        flag = "✓" if item["model_hit"] else ("✗" if item["model_hit"] is False else "-")
        print(f"  {item['num']} {item['home'][:6]}-{item['away'][:6]:6s} "
              f"预测 {item['top'][0][1]:3s}/{item['top'][1][1]:3s} "
              f"实际 {actual['score']:5s} "
              f"让{actual['line']:>2s} 让球结果 {actual['result_hhad']}  让球判断 {flag}")

    summary = {
        "forecast": os.path.relpath(path, REPO_ROOT),
        "matches": scored,
        "one_x_two": {"correct": hits_1x2, "accuracy": round(hits_1x2 / scored, 4),
                      "brier": round(brier / scored, 4),
                      "logloss": round(logloss / scored, 4)},
        "exact_score": {"scoreline1": exact1, "scoreline2": exact2},
        "handicap": {"n": hhad_scored, "model": hhad_model, "market": hhad_market,
                     "draw_tolerant_rule": hhad_draw_rule},
        "rows": rows_out,
        "caveat": "Eight matches is a sample, not evidence: treat it as a log entry.",
    }
    out = os.path.join(REPO_ROOT, "model_validation",
                       os.path.basename(path).replace(".json", "_review.json"))
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(out, "a", encoding="utf-8").write("\n")
    log(f"\nreport: {os.path.relpath(out, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
