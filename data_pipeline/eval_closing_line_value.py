#!/usr/bin/env python3
"""Closing-line value test: can we buy at the open and be right by the close?

Three questions, in order, because each one gates the next:

  1. Is the closing line actually sharper than the opening line? If not, there is nothing
     to capture and the idea dies here.
  2. Can the movement be predicted? A linear model on opening probabilities plus rest days,
     month and league is fitted on the discovery seasons and evaluated on the validation
     seasons. Baseline is "no movement".
  3. Does a betting rule built on the prediction make money? Two measures are reported:
     expected value of the bet evaluated at CLOSING prices (the accepted proof of edge,
     since it is independent of the result) and the realised return at opening prices.

Discovery seasons: 2023-24 + 2024-25. Validation: 2025-26 + 2026-27.

Usage:
    python data_pipeline/eval_closing_line_value.py
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
from datetime import date, datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO_ROOT, "model_validation", "closing_line_value.json")
URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
FEEDS = {"E0": "EPL", "D1": "BUNDESLIGA", "SP1": "LALIGA", "F1": "LIGUE1", "I1": "SERIEA"}
DISCOVERY = ("2324", "2425")
VALIDATION = ("2526", "2627")
OUTCOMES = ("H", "D", "A")


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


def devig(values: list[float]) -> list[float] | None:
    if any(not math.isfinite(v) or v <= 1.0 for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def parse(text: str, league: str, season: str, history: dict) -> list[dict]:
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        date_text = (raw.get("Date") or "").strip()
        try:
            match_date = datetime.strptime(date_text, "%d/%m/%Y").date().isoformat()
        except ValueError:
            continue
        try:
            home_goals, away_goals = int(raw.get("FTHG")), int(raw.get("FTAG"))
            open_odds = [float(raw.get("B365H")), float(raw.get("B365D")), float(raw.get("B365A"))]
            close_odds = [float(raw.get("B365CH")), float(raw.get("B365CD")), float(raw.get("B365CA"))]
        except (TypeError, ValueError):
            continue
        open_probabilities = devig(open_odds)
        close_probabilities = devig(close_odds)
        if not open_probabilities or not close_probabilities:
            continue
        actual = "H" if home_goals > away_goals else ("D" if home_goals == away_goals else "A")
        rest = []
        for team in (raw.get("HomeTeam", "").strip(), raw.get("AwayTeam", "").strip()):
            previous = history.get(team)
            rest.append((date.fromisoformat(match_date) - date.fromisoformat(previous)).days
                        if previous else None)
            history[team] = match_date
        rows.append({
            "season": season, "league": league, "date": match_date, "month": int(match_date[5:7]),
            "actual": actual,
            "open": dict(zip(OUTCOMES, open_probabilities)),
            "close": dict(zip(OUTCOMES, close_probabilities)),
            "open_odds": dict(zip(OUTCOMES, open_odds)),
            "rest_home": rest[0], "rest_away": rest[1],
        })
    return rows


def brier(probabilities: dict[str, float], actual: str) -> float:
    return sum((probabilities[k] - (1.0 if k == actual else 0.0)) ** 2 for k in OUTCOMES)


def vector(row: dict, leagues: list[str], months: list[int]) -> list[float]:
    features = [row["open"][k] for k in OUTCOMES]
    features.append(math.log(row["open"]["H"] / row["open"]["A"]))
    rest_home = row["rest_home"] if row["rest_home"] is not None else 5
    rest_away = row["rest_away"] if row["rest_away"] is not None else 5
    features.extend([max(-6, min(6, rest_home - rest_away)) / 6.0,
                     (min(10, rest_home) - 5) / 5.0, (min(10, rest_away) - 5) / 5.0])
    features.extend([1.0 if row["league"] == lg else 0.0 for lg in leagues[1:]])
    features.extend([1.0 if row["month"] == m else 0.0 for m in months[:-1]])
    features.append(1.0)
    return features


def solve(design: list[list[float]], targets: list[list[float]]) -> list[list[float]]:
    """Ridge-regularised least squares via normal equations (numpy if available)."""
    try:
        import numpy as np
    except ImportError:                                  # pragma: no cover
        raise RuntimeError("numpy is required")
    X = np.asarray(design, dtype=float)
    Y = np.asarray(targets, dtype=float)
    ridge = 1e-4 * np.eye(X.shape[1])
    ridge[-1, -1] = 0.0
    return np.linalg.solve(X.T @ X + ridge, X.T @ Y)


def main() -> int:
    rows: list[dict] = []
    for season in DISCOVERY + VALIDATION:
        for code, league in FEEDS.items():
            text = fetch(season, code)
            if not text:
                log(f"  {season}/{code}: failed")
                continue
            rows.extend(parse(text, league, season, {}))
    log(f"matches: {len(rows)}")

    discovery = [r for r in rows if r["season"] in DISCOVERY]
    validation = [r for r in rows if r["season"] in VALIDATION]

    # ---- question 1: is the close sharper?
    q1 = {}
    for label, key in (("open", "open"), ("close", "close")):
        q1[label] = round(sum(brier(r[key], r["actual"]) for r in validation) / len(validation), 4)
    log(f"Q1 holdout Brier: open={q1['open']:.4f} close={q1['close']:.4f} "
        f"(close sharper by {q1['open'] - q1['close']:+.4f})")

    # ---- question 2: can the movement be predicted?
    leagues = sorted({r["league"] for r in rows})
    months = sorted({r["month"] for r in rows})
    X_train = [vector(r, leagues, months) for r in discovery]
    Y_train = [[r["close"][k] - r["open"][k] for k in OUTCOMES] for r in discovery]
    weights = solve(X_train, Y_train)

    def predict(row: dict) -> list[float]:
        features = vector(row, leagues, months)
        return [sum(w * x for w, x in zip(weights[:, j], features)) for j in range(3)]

    errors, baseline_errors, correct = [], [], 0
    for row in validation:
        prediction = predict(row)
        actual_move = [row["close"][k] - row["open"][k] for k in OUTCOMES]
        errors.extend(abs(p - a) for p, a in zip(prediction, actual_move))
        baseline_errors.extend(abs(a) for a in actual_move)
        # direction of the largest expected move
        if max(range(3), key=lambda i: prediction[i]) == max(range(3), key=lambda i: actual_move[i]):
            correct += 1
    mae = sum(errors) / len(errors)
    baseline_mae = sum(baseline_errors) / len(baseline_errors)
    direction_accuracy = correct / len(validation)
    log(f"Q2 movement MAE: model={mae:.4f} baseline(no move)={baseline_mae:.4f}; "
        f"largest-move direction accuracy={direction_accuracy:.3f}")

    # ---- question 3: does a rule make money?
    def evaluate(rule, label: str, threshold: float) -> dict:
        bets = 0
        clv_ev = 0.0
        realised = 0.0
        for row in validation:
            prediction = predict(row)
            choice = rule(row, prediction, threshold)
            if choice is None:
                continue
            index = OUTCOMES.index(choice)
            bets += 1
            clv_ev += row["open_odds"][choice] * row["close"][choice] - 1.0
            realised += (row["open_odds"][choice] - 1.0) if row["actual"] == choice else -1.0
        if not bets:
            return {"strategy": label, "bets": 0}
        return {"strategy": label, "bets": bets,
                "ev_vs_close": round(clv_ev / bets, 4),
                "realised_roi": round(realised / bets, 4)}

    def model_rule(row, prediction, threshold):
        index = max(range(3), key=lambda i: prediction[i])
        return OUTCOMES[index] if prediction[index] > threshold else None

    def all_home(row, prediction, threshold):
        return "H"

    def all_favourite(row, prediction, threshold):
        return max(OUTCOMES, key=lambda k: row["open"][k])

    strategies = [evaluate(all_home, "always_home", 0.0),
                  evaluate(all_favourite, "always_favourite", 0.0)]
    for threshold in (0.005, 0.01, 0.02, 0.03):
        strategies.append(evaluate(model_rule, f"model_move_gt_{threshold:.3f}", threshold))

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "Closing-line value test. Q1 checks whether closing prices beat opening prices "
            "on the same matches. Q2 fits a linear model of the probability movement (close "
            "minus open) on opening prices, rest days, month and league, then measures forecast "
            "error and how often the model picks the same outcome as the largest realised move. "
            "Q3 bets at opening prices when the predicted move exceeds a threshold and reports "
            "expected value at closing prices (result independent) plus realised return."),
        "discovery_seasons": list(DISCOVERY), "validation_seasons": list(VALIDATION),
        "coverage": {"matches": len(rows), "discovery": len(discovery),
                     "validation": len(validation)},
        "q1_sharper": q1,
        "q2_movement": {"model_mae": round(mae, 4), "baseline_mae": round(baseline_mae, 4),
                        "direction_accuracy": round(direction_accuracy, 4)},
        "q3_strategies": strategies,
        "note": "ev_vs_close is the accepted edge measure: positive means the opening price "
                "was better than the closing line.",
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for item in strategies:
        if item.get("bets"):
            log(f"  {item['strategy']:24s} bets={item['bets']:5d} "
                f"ev_vs_close={item['ev_vs_close']:+.4f} roi={item['realised_roi']:+.4f}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
