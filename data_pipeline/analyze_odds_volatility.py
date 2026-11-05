#!/usr/bin/env python3
"""Which odds component moves the most? Volatility analysis on the radar snapshot.

The cached jc-daily-report page carries three kinds of movement data:

  med   a merged median odds series per match (100+ time points)
  eu    per-bookmaker opening and current 1X2 prices
  ah/ou per-bookmaker opening and current Asian handicap and over/under water

This measures each component's absolute and relative movement so the noisiest one can be
identified. Output is ranked, because the question is specifically "which fluctuates most".

Usage:
    python data_pipeline/analyze_odds_volatility.py
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO_ROOT, "data", "cache", "jc_daily_report.html")
OUT = os.path.join(REPO_ROOT, "model_validation", "odds_volatility.json")


def load_matches() -> list[dict]:
    html = open(CACHE, encoding="utf-8").read()
    match = re.search(r"const\s+M\s*=\s*\[", html)
    if not match:
        raise SystemExit("cached page does not contain the match array")
    index = html.index("[", match.start())
    depth = 0
    in_string = False
    escaped = False
    start = index
    while index < len(html):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    break
        index += 1
    return json.loads(html[start:index + 1])


def main() -> int:
    if not os.path.exists(CACHE):
        print("cache missing: run the radar collector first")
        return 1
    matches = load_matches()
    print(f"matches in snapshot: {len(matches)}")

    # ---- 1X2: opening vs current, per bookmaker
    deltas = {"home": [], "draw": [], "away": []}
    rel = {"home": [], "draw": [], "away": []}
    moved = {"home": 0, "draw": 0, "away": 0}
    quotes = 0
    for match in matches:
        for book in match.get("eu") or []:
            pairs = (("home", book.get("h0"), book.get("h")),
                     ("draw", book.get("d0"), book.get("d")),
                     ("away", book.get("a0"), book.get("a")))
            if any(not isinstance(o, (int, float)) or not isinstance(c, (int, float))
                   for _, o, c in pairs):
                continue
            if any(o <= 1 or c <= 1 for _, o, c in pairs):
                continue
            quotes += 1
            for label, opening, current in pairs:
                deltas[label].append(abs(current - opening))
                rel[label].append(abs(current - opening) / opening)
                if abs(current - opening) > 1e-9:
                    moved[label] += 1

    # ---- Asian handicap water and line
    ah_water, ah_line_changed, ah_total = [], 0, 0
    for match in matches:
        for book in match.get("ah") or []:
            if not isinstance(book.get("home0"), (int, float)):
                continue
            if not isinstance(book.get("home"), (int, float)):
                continue
            ah_total += 1
            ah_water.append(abs(book["home"] - book["home0"]))
            if isinstance(book.get("line0"), (int, float)) and isinstance(book.get("line"), (int, float)):
                if abs(book["line"] - book["line0"]) > 1e-9:
                    ah_line_changed += 1

    # ---- Over/under water
    ou_water, ou_total = [], 0
    for match in matches:
        for book in match.get("ou") or []:
            if isinstance(book.get("big0"), (int, float)) and isinstance(book.get("big"), (int, float)):
                ou_total += 1
                ou_water.append(abs(book["big"] - book["big0"]))

    # ---- median series range
    ranges = {"home": [], "draw": [], "away": []}
    for match in matches:
        series = match.get("med") or []
        if len(series) < 5:
            continue
        for label, key in (("home", "h"), ("draw", "d"), ("away", "a")):
            values = [point[key] for point in series if isinstance(point.get(key), (int, float))]
            if len(values) >= 5:
                ranges[label].append(max(values) - min(values))

    def describe(values):
        if not values:
            return {"n": 0}
        return {"n": len(values), "mean": round(statistics.mean(values), 4),
                "median": round(statistics.median(values), 4),
                "p90": round(sorted(values)[int(len(values) * 0.9)], 4)}

    summary = {
        "source": "data/cache/jc_daily_report.html (one trading day snapshot)",
        "matches": len(matches),
        "bookmaker_quotes": quotes,
        "one_two_X": {
            "absolute_move": {k: describe(v) for k, v in deltas.items()},
            "relative_move": {k: describe(v) for k, v in rel.items()},
            "share_of_quotes_that_moved": {
                k: round(moved[k] / quotes, 4) if quotes else None for k in moved},
        },
        "asian_water": describe(ah_water),
        "asian_line_changed_share": round(ah_line_changed / ah_total, 4) if ah_total else None,
        "over_under_water": describe(ou_water),
        "median_series_range": {k: describe(v) for k, v in ranges.items()},
        "note": ("Relative movement is the fair comparison across components because home, "
                 "draw and away prices sit at different levels."),
    }
    json.dump(summary, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    print(f"\n1X2 开盘到现在的变动（{quotes} 个公司报价）")
    for key in ("home", "draw", "away"):
        absolute = summary["one_two_X"]["absolute_move"][key]
        relative = summary["one_two_X"]["relative_move"][key]
        share = summary["one_two_X"]["share_of_quotes_that_moved"][key]
        print(f"  {key:5s} 绝对 {absolute['mean']:.3f}  相对 {100 * relative['mean']:.2f}%  "
              f"有变化的占比 {100 * share:.1f}%")
    print(f"\n亚盘水位变动: 均值 {summary['asian_water']['mean']:.3f} "
          f"(n={summary['asian_water']['n']})，盘口线变动比例 "
          f"{100 * (summary['asian_line_changed_share'] or 0):.1f}%")
    print(f"大小球水位变动: 均值 {summary['over_under_water']['mean']:.3f} "
          f"(n={summary['over_under_water']['n']})")
    print("\n盘中极差（每场 max-min，中位数值）")
    for key in ("home", "draw", "away"):
        info = summary["median_series_range"][key]
        if info.get("n"):
            print(f"  {key:5s} 中位极差 {info['median']:.3f}  p90 {info['p90']:.3f}")
    print(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
