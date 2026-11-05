#!/usr/bin/env python3
"""Can we identify draws better than the market already does?

The market's draw probability is almost perfectly calibrated (implied 23.99% against an
observed 24.40%), so the only useful question is whether some extra feature separates draws
inside that calibration. This script measures the achievable lift of the candidate rules:

  * the market draw probability itself, in bands
  * the total-goals line (a low-scoring game should be more likely to finish level)
  * the league (draw rates genuinely differ between competitions)
  * recent form: how many of each side's last five matches were draws
  * the interaction of the two strongest signals

Every rule is reported as observed draw rate, coverage and lift over the 24.4% base rate, so a
rule is only worth using if the lift survives at a usable coverage.

Usage:
    python data_pipeline/analyze_draw_detection.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "draw_detection.json")
MIN_BIN = 60


def log(message: str) -> None:
    print(message, flush=True)


def devig(values: list[float]) -> list[float] | None:
    if any(v is None or not math.isfinite(v) or v <= 1.0 for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def band(value: float, edges: list[float]) -> str:
    for edge in edges:
        if value < edge:
            return f"<{edge:g}"
    return f">={edges[-1]:g}"


def rate(rows: list[dict]) -> dict:
    if len(rows) < MIN_BIN:
        return {"n": len(rows)}
    draws = sum(1 for row in rows if row["draw"])
    return {"n": len(rows), "draw_rate": round(draws / len(rows), 4)}


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    raw = [dict(r) for r in connection.execute("""
        SELECT match_date, league, home, away, win, draw, lost, overunder,
               home_score, away_score
        FROM jc_history
        WHERE win > 1 AND draw > 1 AND lost > 1
          AND home_score >= 0 AND away_score >= 0
        ORDER BY match_date, matchnum""")]
    log(f"matches: {len(raw)}")

    history: dict[str, list[bool]] = defaultdict(list)
    recent: dict[str, list[int]] = defaultdict(list)
    samples = []
    for row in raw:
        probabilities = devig([row["win"], row["draw"], row["lost"]])
        if not probabilities:
            continue
        total_line = 2.5
        if row["overunder"]:
            try:
                total_line = float(row["overunder"].split(",")[1])
            except (ValueError, IndexError):
                pass
        home, away = row["home"], row["away"]
        home_hist, away_hist = history[home][-5:], history[away][-5:]
        home_goals, away_goals = recent[home][-5:], recent[away][-5:]
        samples.append({
            "league": row["league"], "date": row["match_date"],
            "month": int(row["match_date"][5:7]),
            "p_draw": probabilities[1],
            "p_home": probabilities[0], "p_away": probabilities[2],
            "gap": abs(probabilities[0] - probabilities[2]),
            "total_line": total_line,
            "draw": row["home_score"] == row["away_score"],
            "home_form_draws": (sum(home_hist) / len(home_hist)) if home_hist else None,
            "away_form_draws": (sum(away_hist) / len(away_hist)) if away_hist else None,
            "home_form_goals": (sum(home_goals) / len(home_goals)) if home_goals else None,
            "away_form_goals": (sum(away_goals) / len(away_goals)) if away_goals else None,
        })
        result_draw = row["home_score"] == row["away_score"]
        history[home].append(result_draw)
        history[away].append(result_draw)
        recent[home].append(row["home_score"] + row["away_score"])
        recent[away].append(row["home_score"] + row["away_score"])

    usable = [s for s in samples]
    base = sum(1 for s in usable if s["draw"]) / len(usable)
    log(f"usable: {len(usable)}, base draw rate {100 * base:.1f}%\n")

    def grouped(key_function):
        groups: dict[str, list[dict]] = defaultdict(list)
        for sample in usable:
            key = key_function(sample)
            if key is not None:
                groups[key].append(sample)
        return {key: rate(items) for key, items in groups.items()}

    payload: dict[str, object] = {
        "base_rate": round(base, 4), "matches": len(usable),
        "rules": {
            "market_draw_probability": grouped(lambda s: band(s["p_draw"], [0.22, 0.24, 0.26, 0.28])),
            "total_goals_line": grouped(lambda s: band(s["total_line"], [2.4, 2.6, 2.9])),
            "league": grouped(lambda s: s["league"]),
            "home_recent_draws": grouped(
                lambda s: band(s["home_form_draws"], [0.2, 0.4]) if s["home_form_draws"] is not None else None),
            "away_recent_draws": grouped(
                lambda s: band(s["away_form_draws"], [0.2, 0.4]) if s["away_form_draws"] is not None else None),
            "strength_gap": grouped(lambda s: band(s["gap"], [0.10, 0.20, 0.30])),
        },
    }

    # combined rule: high market draw probability plus a low total-goals line
    combined = [s for s in usable if s["p_draw"] >= 0.26 and s["total_line"] <= 2.6]
    payload["combined_high_draw_low_goals"] = rate(combined)
    combined2 = [s for s in usable if s["p_draw"] >= 0.28]
    payload["combined_high_draw_only"] = rate(combined2)
    combined3 = [s for s in usable
                 if s["p_draw"] >= 0.26
                 and (s["home_form_draws"] or 0) >= 0.4
                 and (s["away_form_draws"] or 0) >= 0.4]
    payload["combined_high_draw_both_drawing"] = rate(combined3)

    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for name, groups in payload["rules"].items():
        log(f"{name}:")
        ordered = sorted((kv for kv in groups.items() if kv[1].get("n", 0) >= MIN_BIN),
                         key=lambda kv: -kv[1]["draw_rate"])
        for key, value in ordered:
            lift = 100 * (value["draw_rate"] - base)
            log(f"  {key:>10s} n={value['n']:5d}  平局率 {100 * value['draw_rate']:5.1f}%  "
                f"({lift:+.1f}pp 相对基准)")
        log("")
    for name in ("combined_high_draw_low_goals", "combined_high_draw_only",
                 "combined_high_draw_both_drawing"):
        value = payload[name]
        if value.get("n", 0) >= MIN_BIN:
            lift = 100 * (value["draw_rate"] - base)
            log(f"{name}: n={value['n']} 平局率 {100 * value['draw_rate']:.1f}% ({lift:+.1f}pp)")
        else:
            log(f"{name}: n={value.get('n', 0)} (too few)")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
