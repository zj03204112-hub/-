#!/usr/bin/env python3
"""Test the approach as described: last five matches plus European odds.

For every archived jingcai match the model gets two kinds of input:

  market   the de-vigged European prices (胜平负赔率), i.e. what the book already believes
  form     each side's last five results in the archive: points, goals for, goals against,
           win rate and rest days, taken strictly from matches before this one

The forecast starts from the market's log-odds and learns a correction from the form
features, so the question is precisely "does recent form add anything the European prices
have not already priced".

Protocol:
  train 2022-2023   fit the correction weights
  test  2025-2026   evaluate once, never used for fitting
  baselines: market alone, and a form-only model for reference

If the correction weights stay near zero and the test metrics do not improve, the answer is
that form is already priced. If they help, we finally have something the market missed.

Usage:
    python data_pipeline/eval_form_plus_odds.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
TARGET = os.getenv("FORM_TARGET", "1x2")
OUT = os.path.join(REPO_ROOT, "model_validation", f"form_plus_odds_{TARGET}.json")

TRAIN_YEARS = ("2022", "2023")
TEST_YEARS = ("2025", "2026")
WINDOW = 5
LABELS = {"胜": 0, "平": 1, "负": 2}


def log(message: str) -> None:
    print(message, flush=True)


def devig(values: list[float]) -> list[float] | None:
    if any(v is None or not (v > 1.0) for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def logits(probabilities: list[float]) -> list[float]:
    return [math.log(max(1e-9, p)) for p in probabilities]


def softmax(values: list[float]) -> list[float]:
    top = max(values)
    exps = [math.exp(v - top) for v in values]
    total = sum(exps)
    return [v / total for v in exps]


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    matches = [dict(r) for r in connection.execute("""
        SELECT match_date, league, home, away, home_score, away_score, result_spf,
               win, draw, lost, hhad_h, hhad_d, hhad_a, result_hhad
        FROM jc_history
        WHERE home_score >= 0 AND away_score >= 0
        ORDER BY match_date, rowid""")]
    log(f"archive matches: {len(matches)}")

    history: dict[str, list[dict]] = defaultdict(list)
    last_date: dict[str, str] = {}
    samples = []

    for match in matches:
        if TARGET == "hhad":
            prices = devig([match["hhad_h"], match["hhad_d"], match["hhad_a"]])
            result_label = match["result_hhad"]
        else:
            prices = devig([match["win"], match["draw"], match["lost"]])
            result_label = match["result_spf"]
        if result_label not in LABELS:
            prices, result_label = None, None
        home_hist = history.get(match["home"], [])
        away_hist = history.get(match["away"], [])

        if prices and len(home_hist) >= WINDOW and len(away_hist) >= WINDOW:
            def form(team_hist):
                recent = team_hist[-WINDOW:]
                points = sum(3 if h["gf"] > h["ga"] else (1 if h["gf"] == h["ga"] else 0)
                             for h in recent) / WINDOW
                goals_for = sum(h["gf"] for h in recent) / WINDOW
                goals_against = sum(h["ga"] for h in recent) / WINDOW
                wins = sum(1 for h in recent if h["gf"] > h["ga"]) / WINDOW
                return points, goals_for, goals_against, wins

            hp, hgf, hga, hw = form(home_hist)
            ap, agf, aga, aw = form(away_hist)
            rest_home = (date.fromisoformat(match["match_date"])
                         - date.fromisoformat(last_date[match["home"]])).days \
                if match["home"] in last_date else 7
            rest_away = (date.fromisoformat(match["match_date"])
                         - date.fromisoformat(last_date[match["away"]])).days \
                if match["away"] in last_date else 7
            features = [hp - ap, hgf - agf, hga - aga, hw - aw,
                        max(-7, min(7, rest_home - rest_away)) / 7.0]
            samples.append({"date": match["match_date"], "league": match["league"],
                            "prices": prices, "features": features,
                            "actual": LABELS[result_label],
                            "home": match["home"], "away": match["away"]})

        history[match["home"]].append({"gf": match["home_score"], "ga": match["away_score"]})
        history[match["away"]].append({"gf": match["away_score"], "ga": match["home_score"]})
        last_date[match["home"]] = match["match_date"]
        last_date[match["away"]] = match["match_date"]

    train = [s for s in samples if s["date"][:4] in TRAIN_YEARS]
    test = [s for s in samples if s["date"][:4] in TEST_YEARS]
    log(f"usable samples: {len(samples)} (train {len(train)}, test {len(test)})")

    # standardise the features on the training segment only
    count = len(train[0]["features"])
    means = [sum(s["features"][i] for s in train) / len(train) for i in range(count)]
    scales = []
    for i in range(count):
        variance = sum((s["features"][i] - means[i]) ** 2 for s in train) / len(train)
        scales.append(math.sqrt(variance) or 1.0)

    def vector(sample):
        return [(sample["features"][i] - means[i]) / scales[i] for i in range(count)] + [1.0]

    def predict(sample, weights):
        base = logits(sample["prices"])
        x = vector(sample)
        return softmax([base[k] + sum(weights[k][j] * x[j] for j in range(len(x)))
                        for k in range(3)])

    def loss(sample_set, weights):
        total = 0.0
        for sample in sample_set:
            p = predict(sample, weights)
            total -= math.log(max(1e-12, p[sample["actual"]]))
        return total / len(sample_set)

    def metrics(sample_set, weights=None):
        correct = 0
        brier = 0.0
        logloss = 0.0
        for sample in sample_set:
            p = predict(sample, weights) if weights else sample["prices"]
            correct += int(max(range(3), key=lambda i: p[i]) == sample["actual"])
            brier += sum((p[i] - (1.0 if i == sample["actual"] else 0.0)) ** 2 for i in range(3))
            logloss -= math.log(max(1e-12, p[sample["actual"]]))
        n = len(sample_set)
        return {"n": n, "accuracy": round(correct / n, 4), "brier": round(brier / n, 4),
                "logloss": round(logloss / n, 4)}

    # gradient descent on the softmax correction, weights start at zero
    weights = [[0.0] * (count + 1) for _ in range(3)]
    learning_rate = 0.05
    for epoch in range(400):
        gradients = [[0.0] * (count + 1) for _ in range(3)]
        for sample in train:
            p = predict(sample, weights)
            x = vector(sample)
            for k in range(3):
                error = p[k] - (1.0 if k == sample["actual"] else 0.0)
                for j in range(len(x)):
                    gradients[k][j] += error * x[j]
        for k in range(3):
            for j in range(len(gradients[k])):
                weights[k][j] -= learning_rate * gradients[k][j] / len(train)
        if epoch % 100 == 99:
            log(f"  epoch {epoch + 1}: train logloss {loss(train, weights):.4f}")

    # form-only reference: drop the market anchor
    form_only = [[0.0] * (count + 1) for _ in range(3)]
    for epoch in range(400):
        gradients = [[0.0] * (count + 1) for _ in range(3)]
        for sample in train:
            x = vector(sample)
            p = softmax([sum(form_only[k][j] * x[j] for j in range(len(x))) for k in range(3)])
            for k in range(3):
                error = p[k] - (1.0 if k == sample["actual"] else 0.0)
                for j in range(len(x)):
                    gradients[k][j] += error * x[j]
        for k in range(3):
            for j in range(len(gradients[k])):
                form_only[k][j] -= learning_rate * gradients[k][j] / len(train)

    def metrics_form_only(sample_set):
        correct = 0
        brier = 0.0
        logloss = 0.0
        for sample in sample_set:
            x = vector(sample)
            p = softmax([sum(form_only[k][j] * x[j] for j in range(len(x))) for k in range(3)])
            correct += int(max(range(3), key=lambda i: p[i]) == sample["actual"])
            brier += sum((p[i] - (1.0 if i == sample["actual"] else 0.0)) ** 2 for i in range(3))
            logloss -= math.log(max(1e-12, p[sample["actual"]]))
        n = len(sample_set)
        return {"n": n, "accuracy": round(correct / n, 4), "brier": round(brier / n, 4),
                "logloss": round(logloss / n, 4)}

    summary = {
        "definition": (
            "Last-five-match form plus European odds, as described. The market's log-probabilities "
            "are the starting point and a linear correction on form features (points, goals for, "
            "goals against, win rate, rest days, all as home minus away) is fitted on 2022-2023 "
            "and evaluated once on 2025-2026."),
        "window": WINDOW,
        "features": ["points_diff", "goals_for_diff", "goals_against_diff",
                     "win_rate_diff", "rest_days_diff"],
        "train": metrics(train),
        "train_with_form": metrics(train, weights),
        "test_market": metrics(test),
        "test_market_plus_form": metrics(test, weights),
        "test_form_only": metrics_form_only(test),
        "learned_weights": [[round(w, 4) for w in row] for row in weights],
    }
    json.dump(summary, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for key in ("test_market", "test_market_plus_form", "test_form_only"):
        m = summary[key]
        log(f"  {key:22s} n={m['n']:5d} acc={m['accuracy']:.3f} "
            f"brier={m['brier']:.4f} logloss={m['logloss']:.4f}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
