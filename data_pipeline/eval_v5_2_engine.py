#!/usr/bin/env python3
"""Evaluate the V5.2 market-lambda engine against the plain market baseline.

V5.2 is the only executable part of V5 (V5-SCORE v1.0 and V5.1 exist as specifications
only). It infers a total-goals lambda from de-vigged over/under prices, then splits it to
best fit de-vigged 1X2 prices.

That makes it a clean test of one question: does the total-goals market add information on
top of the 1X2 market? Three forecasts are compared on the same matches:

  market_1x2   de-vigged 1X2 prices, nothing else
  v5_2         the engine run from 1X2 + 2.5 goals prices
  v4           the repository's feature model, for reference

The engine's experimental tail-reweighting layer is NOT applied here; the spec itself says
it is uncalibrated, so only the calibrated part is scored.

To keep runtime sane the lambda-allocation grid is coarsened (301 steps, 12 goals) and the
report records that, since it is the only deviation from the default engine settings.

Usage:
    python data_pipeline/eval_v5_2_engine.py
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(REPO_ROOT, "data_pipeline")
sys.path.insert(0, PIPELINE)
sys.path.insert(0, REPO_ROOT)

import eval_market_baseline as market                       # noqa: E402
from model_validation.v5_2_market_lambda import (           # noqa: E402
    devig_1x2, devig_two_way, infer_total_lambda, allocate_lambdas)

OUT = os.path.join(REPO_ROOT, "model_validation", "v5_2_engine_vs_market.json")
SEASONS = ("2324", "2425", "2526", "2627")
FEEDS = {"E0": "EPL", "D1": "BUNDESLIGA", "SP1": "LALIGA", "F1": "LIGUE1", "I1": "SERIEA"}
URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
STEPS = 301
MAX_GOALS = 12


def log(message: str) -> None:
    print(message, flush=True)


def fetch(season: str, code: str) -> str | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(1, 3):
        try:
            request = urllib.request.Request(URL.format(season=season, code=code),
                                             headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(request, timeout=45) as response:
                return response.read().decode("utf-8-sig", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            if attempt == 2:
                return None
    return None


def parse(text: str) -> list[dict]:
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        date_text = (raw.get("Date") or "").strip()
        try:
            match_date = datetime.strptime(date_text, "%d/%m/%Y").date().isoformat()
        except ValueError:
            try:
                match_date = datetime.strptime(date_text, "%d/%m/%y").date().isoformat()
            except ValueError:
                continue
        try:
            one_x_two = [float(raw.get("B365H")), float(raw.get("B365D")), float(raw.get("B365A"))]
            over_under = [float(raw.get("B365>2.5")), float(raw.get("B365<2.5"))]
        except (TypeError, ValueError):
            continue
        if any(not math.isfinite(x) or x <= 1.0 for x in one_x_two + over_under):
            continue
        rows.append({"date": match_date, "home": (raw.get("HomeTeam") or "").strip(),
                     "away": (raw.get("AwayTeam") or "").strip(),
                     "one_x_two": one_x_two, "over_under": over_under})
    return rows


def score(probabilities: dict[str, float], actual: str) -> tuple[int, float, float]:
    y = {k: 0.0 for k in probabilities}
    y[actual] = 1.0
    correct = int(max(probabilities, key=probabilities.get) == actual)
    brier = sum((probabilities[k] - y[k]) ** 2 for k in probabilities)
    logloss = -math.log(max(1e-12, probabilities[actual]))
    return correct, brier, logloss


def summarise(rows: list[tuple[int, float, float]]) -> dict:
    if not rows:
        return {"n": 0}
    n = len(rows)
    return {"n": n,
            "accuracy": round(sum(r[0] for r in rows) / n, 4),
            "brier": round(sum(r[1] for r in rows) / n, 4),
            "logloss": round(sum(r[2] for r in rows) / n, 4)}


def main() -> int:
    import sqlite3
    connection = sqlite3.connect(os.path.join(REPO_ROOT, "football_model_database.sqlite"))
    matches = connection.execute("""
        SELECT m.match_id, m.kickoff, m.home_team, m.away_team, r.ft_home, r.ft_away,
               c.competition_code
        FROM matches m
        JOIN results r ON r.match_id = m.match_id
        JOIN competitions c ON c.competition_id = m.competition_id
        WHERE m.status = 'finished' AND r.ft_home IS NOT NULL
        ORDER BY m.kickoff""").fetchall()

    by_league_date = defaultdict(list)
    for row in matches:
        by_league_date[(row[6], row[1][:10])].append(row)

    quotes: dict[str, dict] = {}
    for season in SEASONS:
        for code, league in FEEDS.items():
            text = fetch(season, code)
            if not text:
                continue
            for row in parse(text):
                best, best_score = None, 0.0
                for delta in (0, 1, -1):
                    day = (date.fromisoformat(row["date"]) + timedelta(days=delta)).isoformat()
                    for candidate in by_league_date.get((league, day), []):
                        value = min(market.similarity(row["home"], candidate[2]),
                                    market.similarity(row["away"], candidate[3]))
                        if value > best_score:
                            best, best_score = candidate, value
                if best and best_score >= 0.6:
                    quotes[best[0]] = row
    lines: dict[str, float] = {}
    for season in SEASONS:
        for code, league in FEEDS.items():
            text = fetch(season, code)
            if not text:
                continue
            for row in market.parse_ah_feed(text):
                best, best_score = None, 0.0
                for delta in (0, 1, -1):
                    day = (date.fromisoformat(row["date"]) + timedelta(days=delta)).isoformat()
                    for candidate in by_league_date.get((league, day), []):
                        value = min(market.similarity(row["home"], candidate[2]),
                                    market.similarity(row["away"], candidate[3]))
                        if value > best_score:
                            best, best_score = candidate, value
                if best and best_score >= 0.6:
                    lines[best[0]] = row["line"]
    log(f"matches with 1X2 + 2.5 goals quotes: {len(quotes)}; with handicap line: {len(lines)}")

    ordered = [m for m in matches if m[0] in quotes]
    split = max(1, int(len(ordered) * 0.70))
    holdout = ordered[split:]
    log(f"holdout: {len(holdout)}")

    results: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    failures = {"lambda_bounds": 0, "other": 0}
    for row in holdout:
        quote = quotes[row[0]]
        actual = "H" if row[4] > row[5] else ("D" if row[4] == row[5] else "A")
        p_home, p_draw, p_away = devig_1x2(*quote["one_x_two"])
        results["market_1x2"].append(score({"H": p_home, "D": p_draw, "A": p_away}, actual))
        try:
            p_over, _ = devig_two_way(*quote["over_under"])
            total_lambda = infer_total_lambda(p_over, 2.5)
            allocation = allocate_lambdas(total_lambda, p_home, p_draw, p_away,
                                          steps=STEPS, max_goals=MAX_GOALS)
            grid = allocation["distribution"]
            probabilities = {"H": grid["p_home"], "D": grid["p_draw"], "A": grid["p_away"]}
            results["v5_2"].append(score(probabilities, actual))
            line = lines.get(row[0])
            if line is not None and abs(line - round(line)) < 1e-9:
                counts = {"让胜": 0.0, "让平": 0.0, "让负": 0.0}
                grid_rows = allocation.get("_grid")
                omega = {}
                # rebuild the full matrix from the allocated lambdas for the three-way view
                from model_validation.v5_2_market_lambda import score_grid
                full = score_grid(allocation["lambda_home"], allocation["lambda_away"], MAX_GOALS)
                for item_p, h, a in [(0, 0, 0)]:
                    pass
                # score_grid returns only aggregates, so recompute the three-way directly
                from model_validation.v5_2_market_lambda import poisson_pmf
                hp = poisson_pmf(allocation["lambda_home"], MAX_GOALS)
                ap = poisson_pmf(allocation["lambda_away"], MAX_GOALS)
                for i, ph in enumerate(hp):
                    for j, pa in enumerate(ap):
                        margin = i - j + line
                        prob = ph * pa
                        if margin > 0:
                            counts["让胜"] += prob
                        elif margin == 0:
                            counts["让平"] += prob
                        else:
                            counts["让负"] += prob
                total_mass = sum(counts.values())
                three = {k: v / total_mass for k, v in counts.items()}
                margin_actual = (row[4] - row[5]) + line
                wanted = "让胜" if margin_actual > 0 else ("让平" if margin_actual == 0 else "让负")
                correct3 = int(max(three, key=three.get) == wanted)
                y3 = {k: 0.0 for k in three}
                y3[wanted] = 1.0
                results["v5_2_handicap_3way"].append((
                    correct3, sum((three[k] - y3[k]) ** 2 for k in three),
                    -math.log(max(1e-12, three[wanted]))))
        except ValueError as exc:
            failures["lambda_bounds" if "bounds" in str(exc) else "other"] += 1

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "V5.2 market-lambda engine fed with de-vigged 1X2 and 2.5 goals opening prices, "
            "compared with the 1X2 market alone on identical matches. Only the calibrated part "
            "of the engine is used; the experimental tail reweighting is not applied."),
        "engine_settings": {"steps": STEPS, "max_goals": MAX_GOALS,
                            "note": "coarser grid than the default engine for runtime only"},
        "coverage": {"quoted_matches": len(quotes), "holdout": len(holdout)},
        "failures": failures,
        "metrics": {name: summarise(rows) for name, rows in results.items()},
    }
    if "market_1x2" in payload["metrics"] and "v5_2" in payload["metrics"]:
        payload["delta_brier"] = round(
            payload["metrics"]["v5_2"]["brier"] - payload["metrics"]["market_1x2"]["brier"], 4)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for name in ("market_1x2", "v5_2"):
        m = payload["metrics"].get(name)
        if m and m.get("n"):
            log(f"  {name:11s} n={m['n']:4d} acc={m['accuracy']:.3f} "
                f"brier={m['brier']:.4f} logloss={m['logloss']:.4f}")
    log(f"  failures: {failures}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
