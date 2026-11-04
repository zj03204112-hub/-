#!/usr/bin/env python3
"""Handicap baseline: our score matrix vs the Asian handicap market.

The product is a handicap model, so this is the comparison that matters. football-data
provides the line (AHh) and two-way prices (B365AHH/AHA, AvgAHH/AHA), which de-vig into
the market's cover probability for the home side.

Settlement follows Asian rules:
  * half lines   -> binary, home covers when margin + line > 0
  * whole lines  -> push when the adjusted margin is zero (counted as half a win)
  * quarter lines-> the stake splits across the two neighbouring lines, so the realised
                    win fraction is the mean of the two legs

Both the market probability and the model probability use the same settlement convention,
so the comparison is like for like.

Usage:
    python data_pipeline/eval_handicap_baseline.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(REPO_ROOT, "data_pipeline")
sys.path.insert(0, PIPELINE)

import eval_lambda_baselines as lam          # noqa: E402  (shared lambda/matrix code)
import eval_market_baseline as market        # noqa: E402  (shared feed parsing and matching)

DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "handicap_baseline_comparison.json")
COMPARISON = os.path.join(REPO_ROOT, "model_validation", "lambda_baseline_comparison.json")
WINDOW = 5
DECAY = 0.0


def log(message: str) -> None:
    print(message, flush=True)


def legs_for(line: float) -> list[float]:
    if abs(line * 4 - round(line * 4)) < 1e-9 and round(line * 4) % 2 != 0:
        return [line - 0.25, line + 0.25]
    return [line]


def realised_fraction(margin: int, line: float) -> float:
    values = []
    for leg in legs_for(line):
        adjusted = margin + leg
        values.append(1.0 if adjusted > 0 else (0.0 if adjusted < 0 else 0.5))
    return sum(values) / len(values)


def model_cover_probability(grid, line: float) -> float:
    """Probability the home side covers, under the same push/half-win convention."""
    values = []
    for leg in legs_for(line):
        probability = 0.0
        for i in range(len(grid)):
            for j in range(len(grid)):
                adjusted = i - j + leg
                if adjusted > 0:
                    probability += grid[i][j]
                elif abs(adjusted) < 1e-9:
                    probability += 0.5 * grid[i][j]
        values.append(probability)
    return sum(values) / len(values)


def brier(p: float, v: float) -> float:
    return (p - v) ** 2


def cross_entropy(p: float, v: float) -> float:
    q = min(max(p, 1e-9), 1 - 1e-9)
    return -(v * math.log(q) + (1 - v) * math.log(1 - q))


def summarise(rows: list[tuple[float, float]]) -> dict:
    if not rows:
        return {"n": 0}
    n = len(rows)
    decisive = [(p, v) for p, v in rows if v != 0.5]
    return {
        "n": n,
        "brier": round(sum(brier(p, v) for p, v in rows) / n, 4),
        "cross_entropy": round(sum(cross_entropy(p, v) for p, v in rows) / n, 4),
        "accuracy": round(sum(1 for p, v in decisive if (p > 0.5) == (v > 0.5)) / len(decisive), 4)
        if decisive else None,
        "decisive_n": len(decisive),
        "mean_prob": round(sum(p for p, _ in rows) / n, 4),
        "actual_win_rate": round(sum(v for _, v in rows) / n, 4),
    }


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    matches = [dict(r) for r in connection.execute("""
        SELECT match_key, league_code, match_date, home_team, away_team, score
        FROM dsl_matches WHERE score LIKE '%-%' ORDER BY match_date""")]

    xg = defaultdict(float)
    for row in connection.execute("SELECT match_key, team, xg FROM dsl_shots"):
        try:
            xg[(row["match_key"], row["team"])] += float(row["xg"])
        except (TypeError, ValueError):
            continue

    history_goals = defaultdict(list)
    history_xg = defaultdict(list)
    models: dict[str, dict[str, tuple[float, float]]] = {"goals": {}, "xg": {}}
    for match in matches:
        home, away = match["home_team"], match["away_team"]
        fh, fa = (int(x) for x in match["score"].split("-"))
        hg, ag = history_goals[home], history_goals[away]
        hx, ax = history_xg[home], history_xg[away]
        if len(hg) >= lam.MIN_HISTORY and len(ag) >= lam.MIN_HISTORY:
            hgf, hga = lam.decayed(hg, DECAY, WINDOW)
            agf, aga = lam.decayed(ag, DECAY, WINDOW)
            models["goals"][match["match_key"]] = lam.lambdas(hgf, hga, agf, aga)
        if len(hx) >= lam.MIN_HISTORY and len(ax) >= lam.MIN_HISTORY:
            hgf, hga = lam.decayed(hx, DECAY, WINDOW)
            agf, aga = lam.decayed(ax, DECAY, WINDOW)
            models["xg"][match["match_key"]] = lam.lambdas(hgf, hga, agf, aga)
        history_goals[home].insert(0, (fh, fa))
        history_goals[away].insert(0, (fa, fh))
        hxv, axv = xg.get((match["match_key"], home)), xg.get((match["match_key"], away))
        if hxv is not None and axv is not None:
            history_xg[home].insert(0, (hxv, axv))
            history_xg[away].insert(0, (axv, hxv))

    cutoff = None
    if os.path.exists(COMPARISON):
        cutoff = json.load(open(COMPARISON, encoding="utf-8")).get("settings", {}).get("holdout_start")
    log(f"holdout cutoff: {cutoff}")

    by_league_date = defaultdict(list)
    for match in matches:
        by_league_date[(match["league_code"], match["match_date"])].append(match)

    from datetime import date, timedelta
    rows: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_league: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    by_line: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    unmatched = 0
    matched = 0

    for code, league in market.FEEDS.items():
        text = market.fetch_csv(code)
        if not text:
            continue
        for raw in market.parse_ah_feed(text):
            best, best_score = None, 0.0
            for delta in (0, 1, -1):
                day = (date.fromisoformat(raw["date"]) + timedelta(days=delta)).isoformat()
                for candidate in by_league_date.get((league, day), []):
                    score = min(market.similarity(raw["home"], candidate["home_team"]),
                                market.similarity(raw["away"], candidate["away_team"]))
                    if score > best_score:
                        best, best_score = candidate, score
            if not best or best_score < 0.55:
                unmatched += 1
                continue
            matched += 1
            if cutoff and best["match_date"] <= cutoff:
                continue
            key = best["match_key"]
            line = raw["line"]
            fh, fa = (int(x) for x in best["score"].split("-"))
            fraction = realised_fraction(fh - fa, line)
            quarter = "quarter" if len(legs_for(line)) == 2 else ("whole" if abs(line - round(line)) < 1e-9 else "half")
            for label, probability in (("market_b365", raw.get("p_b365")),
                                       ("market_avg", raw.get("p_avg"))):
                if probability is None:
                    continue
                rows[label].append((probability, fraction))
                by_league[label][league].append((probability, fraction))
                by_line[label][quarter].append((probability, fraction))
            for variant in ("goals", "xg"):
                lambdas = models[variant].get(key)
                if not lambdas:
                    continue
                grid = lam.matrix(*lambdas)
                probability = model_cover_probability(grid, line)
                rows[f"model_{variant}"].append((probability, fraction))
                by_league[f"model_{variant}"][league].append((probability, fraction))
                by_line[f"model_{variant}"][quarter].append((probability, fraction))

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "Asian handicap comparison on the same chronological holdout. Market cover "
            "probabilities come from de-vigged opening prices; model cover probabilities come "
            "from the same 8x8 score matrix used in the 1X2 evaluation. Settlement follows "
            "Asian rules, with quarter lines split across the two neighbouring lines and pushes "
            "counted as half a win for both sides."),
        "holdout_start": cutoff,
        "window": WINDOW,
        "decay": DECAY,
        "coverage": {"matched_fixtures": matched, "unmatched_feed_rows": unmatched},
        "metrics": {label: summarise(values) for label, values in rows.items()},
        "by_league": {label: {lg: summarise(v) for lg, v in per.items()}
                      for label, per in by_league.items()},
        "by_line_type": {label: {lg: summarise(v) for lg, v in per.items()}
                         for label, per in by_line.items()},
        "caveats": [
            "Opening prices, not T-12h snapshots; the market side is therefore understated.",
            "K League 1, J1 and the A-League have no odds feed and are excluded.",
            "Accuracy counts only decisive outcomes (a push is not a hit or a miss).",
        ],
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for label in ("market_b365", "market_avg", "model_goals", "model_xg"):
        m = payload["metrics"].get(label)
        if m and m.get("n"):
            log(f"  {label:12s} n={m['n']:4d} brier={m['brier']:.4f} "
                f"ce={m['cross_entropy']:.4f} acc={m['accuracy']} decisive={m['decisive_n']}")
    log(f"matched={matched} unmatched={unmatched}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
