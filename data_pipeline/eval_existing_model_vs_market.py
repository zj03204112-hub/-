#!/usr/bin/env python3
"""Evaluate the repository's existing V4 model against the market.

The legacy database covers 2023-11 to 2026-09, which is far more than the 2026/27 detail
layer, so this experiment brings its own market baseline: historical football-data seasons
(2023-24 .. 2026-27) supply opening 1X2 prices for the same fixtures.

Both sides are scored on identical matches with a chronological holdout:

  v3 / v4     compare_models.run_model, i.e. the model as it exists in the repository
  market      de-vigged B365 opening prices
  blend       model and market mixed with a weight fitted on the training segment only

Usage:
    python data_pipeline/eval_existing_model_vs_market.py
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(REPO_ROOT, "data_pipeline")
sys.path.insert(0, PIPELINE)

import compare_models as cm                  # noqa: E402
import eval_market_baseline as market        # noqa: E402

DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "existing_model_vs_market.json")
SEASONS = ("2324", "2425", "2526", "2627")
FEEDS = {"E0": "EPL", "D1": "BUNDESLIGA", "SP1": "LALIGA", "F1": "LIGUE1", "I1": "SERIEA"}
URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"


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
            odds = [float(raw.get("B365H")), float(raw.get("B365D")), float(raw.get("B365A"))]
        except (TypeError, ValueError):
            continue
        probabilities = market.devig(odds)
        if not probabilities:
            continue
        rows.append({"date": match_date, "home": (raw.get("HomeTeam") or "").strip(),
                     "away": (raw.get("AwayTeam") or "").strip(),
                     "p": {"H": probabilities[0], "D": probabilities[1], "A": probabilities[2]}})
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
    connection = sqlite3.connect(DB)
    matches = connection.execute("""
        SELECT m.match_id, m.kickoff, m.home_team, m.away_team, r.ft_home, r.ft_away,
               c.competition_code
        FROM matches m
        JOIN results r ON r.match_id = m.match_id
        JOIN competitions c ON c.competition_id = m.competition_id
        WHERE m.status = 'finished' AND r.ft_home IS NOT NULL
        ORDER BY m.kickoff""").fetchall()
    log(f"legacy finished matches: {len(matches)}")

    by_league_date = defaultdict(list)
    for row in matches:
        by_league_date[(row[6], row[1][:10])].append(row)

    market_probs: dict[str, dict[str, float]] = {}
    seen = 0
    for season in SEASONS:
        for code, league in FEEDS.items():
            text = fetch(season, code)
            if not text:
                log(f"  {season}/{code}: download failed")
                continue
            rows = parse(text)
            hits = 0
            for row in rows:
                seen += 1
                best, best_score = None, 0.0
                for delta in (0, 1, -1):
                    day = (date.fromisoformat(row["date"]) + timedelta(days=delta)).isoformat()
                    for candidate in by_league_date.get((league, day), []):
                        value = min(market.similarity(row["home"], candidate[2]),
                                    market.similarity(row["away"], candidate[3]))
                        if value > best_score:
                            best, best_score = candidate, value
                if best and best_score >= 0.6:
                    market_probs[best[0]] = row["p"]
                    hits += 1
            log(f"  {season}/{code}: {len(rows)} rows, matched {hits}")

    pairs = [m for m in matches if m[0] in market_probs]
    log(f"matches with market odds: {len(pairs)} of {len(matches)}")
    if not pairs:
        return 1

    split = max(1, int(len(pairs) * 0.70))
    train, holdout = pairs[:split], pairs[split:]
    log(f"train {len(train)} / holdout {len(holdout)} (cutoff {train[-1][1][:10]})")

    def actual(row) -> str:
        return "H" if row[4] > row[5] else ("D" if row[4] == row[5] else "A")

    model_cache: dict[str, dict[str, dict[str, float]]] = {"v3": {}, "v4": {}}
    for row in pairs:
        for variant in ("v3", "v4"):
            probabilities, _ = cm.run_model(connection, variant, tuple(row[:6]))
            model_cache[variant][row[0]] = probabilities
    log("model probabilities computed for the paired sample")

    def blend(model: dict[str, float], prior: dict[str, float], weight: float) -> dict[str, float]:
        mixed = {k: (1 - weight) * model[k] + weight * prior[k] for k in ("H", "D", "A")}
        total = sum(mixed.values())
        return {k: v / total for k, v in mixed.items()}

    best = {}
    for variant in ("v3", "v4"):
        best_weight, best_brier = 0.0, float("inf")
        for step in range(21):
            weight = step / 20
            total = sum(score(blend(model_cache[variant][r[0]], market_probs[r[0]], weight),
                              actual(r))[1] for r in train)
            if total / len(train) < best_brier:
                best_brier, best_weight = total / len(train), weight
        best[variant] = {"weight": best_weight, "train_brier": round(best_brier, 4)}
        log(f"  {variant}: blend weight {best_weight:.2f} (train brier {best_brier:.4f})")

    results: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    by_league: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in holdout:
        outcome = actual(row)
        league = row[6]
        market_row = market_probs[row[0]]
        results["market"].append(score(market_row, outcome))
        by_league["market"][league].append(score(market_row, outcome))
        for variant in ("v3", "v4"):
            probabilities = model_cache[variant][row[0]]
            results[variant].append(score(probabilities, outcome))
            by_league[variant][league].append(score(probabilities, outcome))
            mixed = blend(probabilities, market_row, best[variant]["weight"])
            results[f"{variant}_blend"].append(score(mixed, outcome))
            by_league[f"{variant}_blend"][league].append(score(mixed, outcome))

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "The repository's existing V3/V4 models measured against de-vigged opening "
            "bookmaker prices on the legacy database sample (2023-11 to 2026-09), matched "
            "via historical football-data seasons. Chronological split, last 30% held out. "
            "Blend weights fitted on the training segment only."),
        "seasons": list(SEASONS),
        "feeds": FEEDS,
        "coverage": {"legacy_matches": len(matches), "with_market": len(pairs),
                     "train": len(train), "holdout": len(holdout),
                     "cutoff": train[-1][1][:10]},
        "fitted": best,
        "metrics": {name: summarise(rows) for name, rows in results.items()},
        "by_league": {name: {lg: summarise(v) for lg, v in per.items()}
                      for name, per in by_league.items()},
        "caveats": [
            "Opening prices, not T-12h snapshots.",
            "J1 and K League 1 have no odds in this feed and are excluded.",
            "Model lambdas come from compare_models.py unchanged, including its lineup and "
            "schedule tables, so this measures the model as shipped rather than a variant.",
        ],
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for name in ("market", "v3", "v4", "v3_blend", "v4_blend"):
        m = payload["metrics"].get(name)
        if m and m.get("n"):
            log(f"  {name:10s} n={m['n']:4d} acc={m['accuracy']:.3f} "
                f"brier={m['brier']:.4f} logloss={m['logloss']:.4f}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
