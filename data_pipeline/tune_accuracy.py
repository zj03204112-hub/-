#!/usr/bin/env python3
"""Measure what actually raises accuracy, on the legacy sample.

Three levers are tested against the shipped V4 model, each fitted on the training segment
and evaluated once on the holdout:

  calibration     temperature scaling and a draw bias, both fitted on train
  selective       abstain when the top probability is below a threshold, and report the
                  accuracy/coverage trade-off (this is how a real betting filter behaves)
  market fusion   blend with de-vigged opening prices

V4 probabilities are cached so the expensive model pass runs once.

Usage:
    python data_pipeline/tune_accuracy.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(REPO_ROOT, "data_pipeline")
sys.path.insert(0, PIPELINE)

import compare_models as cm                  # noqa: E402
import eval_market_baseline as market        # noqa: E402
import eval_existing_model_vs_market as legacy   # noqa: E402

DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "accuracy_levers.json")
CACHE = os.path.join(REPO_ROOT, "model_validation", "v4_probability_cache.json")


def log(message: str) -> None:
    print(message, flush=True)


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

    by_league_date = defaultdict(list)
    for row in matches:
        by_league_date[(row[6], row[1][:10])].append(row)

    quotes: dict[str, dict[str, float]] = {}
    for season in legacy.SEASONS:
        for code, league in legacy.FEEDS.items():
            text = legacy.fetch(season, code)
            if not text:
                continue
            for row in legacy.parse(text):
                best, best_score = None, 0.0
                for delta in (0, 1, -1):
                    from datetime import date, timedelta
                    day = (date.fromisoformat(row["date"]) + timedelta(days=delta)).isoformat()
                    for candidate in by_league_date.get((league, day), []):
                        value = min(market.similarity(row["home"], candidate[2]),
                                    market.similarity(row["away"], candidate[3]))
                        if value > best_score:
                            best, best_score = candidate, value
                if best and best_score >= 0.6:
                    quotes[best[0]] = row["p"]
    log(f"quoted matches: {len(quotes)}")

    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
        log(f"loaded cached V4 probabilities: {len(cache)}")
    else:
        cache = {}
    missing = [m for m in matches if m[0] in quotes and m[0] not in cache]
    if missing:
        log(f"computing V4 probabilities for {len(missing)} matches ...")
        for index, row in enumerate(missing, start=1):
            probabilities, _ = cm.run_model(connection, "v4", tuple(row[:6]))
            cache[row[0]] = probabilities
            if index % 500 == 0:
                log(f"  {index}/{len(missing)}")
        json.dump(cache, open(CACHE, "w", encoding="utf-8"))

    pairs = [m for m in matches if m[0] in quotes and m[0] in cache]
    split = max(1, int(len(pairs) * 0.70))
    train, holdout = pairs[:split], pairs[split:]

    def actual(row) -> str:
        return "H" if row[4] > row[5] else ("D" if row[4] == row[5] else "A")

    def metrics(rows) -> dict:
        if not rows:
            return {"n": 0}
        n = len(rows)
        return {"n": n,
                "accuracy": round(sum(r[0] for r in rows) / n, 4),
                "brier": round(sum(r[1] for r in rows) / n, 4),
                "logloss": round(sum(r[2] for r in rows) / n, 4)}

    def evaluate(name: str, transform) -> dict:
        rows = [legacy.score(transform(cache[r[0]]), actual(r)) for r in holdout]
        return {"name": name, **metrics(rows)}

    train_items = [(cache[r[0]], actual(r)) for r in train]
    temperature = cm.fit_temperature(train_items)
    draw_temp, draw_bias = cm.fit_draw_calibration(train_items)
    log(f"fitted on train: temperature={temperature:.3f}, draw shift={draw_bias:+.4f}")

    results = [evaluate("v4_baseline", lambda p: p),
               evaluate("v4_temperature", lambda p: cm.apply_temperature(p, temperature)),
               evaluate("v4_draw_calibration",
                        lambda p: cm.apply_draw_bias(p, draw_temp, draw_bias))]

    def both(p):
        return cm.apply_draw_bias(cm.apply_temperature(p, temperature), draw_temp, draw_bias)

    results.append(evaluate("v4_both", both))

    market_rows = [legacy.score(quotes[r[0]], actual(r)) for r in holdout]
    results.append({"name": "market", **metrics(market_rows)})

    best_weight, best_brier = 1.0, float("inf")
    for step in range(21):
        weight = step / 20
        total = 0.0
        for row in train:
            model = cm.apply_temperature(cache[row[0]], temperature)
            prior = quotes[row[0]]
            mixed = {k: (1 - weight) * model[k] + weight * prior[k] for k in ("H", "D", "A")}
            total += legacy.score({k: v / sum(mixed.values()) for k, v in mixed.items()},
                                  actual(row))[1]
        if total / len(train) < best_brier:
            best_brier, best_weight = total / len(train), weight
    blended_rows = []
    for row in holdout:
        model = cm.apply_temperature(cache[row[0]], temperature)
        prior = quotes[row[0]]
        mixed = {k: (1 - best_weight) * model[k] + best_weight * prior[k] for k in ("H", "D", "A")}
        blended_rows.append(legacy.score({k: v / sum(mixed.values()) for k, v in mixed.items()},
                                         actual(row)))
    results.append({"name": "v4_temperature_blend_market", **metrics(blended_rows)})

    # selective prediction on the calibrated model
    calibrated = [(cm.apply_temperature(cache[r[0]], temperature), actual(r)) for r in holdout]
    curve = []
    for threshold in (0.0, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        picked = [(p, a) for p, a in calibrated if max(p.values()) >= threshold]
        if not picked:
            continue
        correct = sum(1 for p, a in picked if max(p, key=p.get) == a)
        curve.append({"threshold": threshold, "coverage": round(len(picked) / len(calibrated), 4),
                      "accuracy": round(correct / len(picked), 4), "n": len(picked)})

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "Accuracy levers measured on the legacy sample with a chronological holdout. "
            "Calibration parameters are fitted on the training segment only. Selective "
            "prediction reports accuracy as a function of how many matches are actually bet."),
        "coverage": {"paired": len(pairs), "train": len(train), "holdout": len(holdout),
                     "cutoff": train[-1][1][:10]},
        "fitted": {"temperature": round(temperature, 4), "draw_shift": round(draw_bias, 4),
                   "blend_weight": best_weight},
        "results": results,
        "selective_curve": curve,
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for item in results:
        log(f"  {item['name']:28s} n={item.get('n', 0):4d} acc={item.get('accuracy')} "
            f"brier={item.get('brier')}")
    log("  selective curve: " + ", ".join(
        f"{c['threshold']:.2f}->{c['accuracy']:.3f}@{c['coverage']:.0%}" for c in curve))
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
