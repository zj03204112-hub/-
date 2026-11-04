#!/usr/bin/env python3
"""Evaluate the pipeline the user described, component by component.

Flow under test:
  1. recent attacking / defensive xG for both sides          -> lambda
  2. predicted lineup                                        -> lineup adjustment
  3. match importance                                        -> tested as rest-days proxy
  4. handicap line and prices                                -> market prior
  5. outputs: 1X2, top two scorelines, and the competitive-lottery (jingcai) handicap
     three-way result derived mechanically from the score matrix

Each component is added separately so its contribution is visible:

  model_xg     recent xG only
  market       de-vigged opening 1X2 prices only
  blend        model and market mixed, weight fitted on the training segment only
  blend_squad  blend plus a starting-XI continuity adjustment. This one is deliberately
               LEAKY: continuity uses the actual XI of the match being predicted, which is
               only known about an hour before kickoff. It is therefore reported as an upper
               bound on what perfect lineup knowledge could add, not as a usable feature.

The competitiveness handicap three-way (win / push / lose after the line) is produced from
the same score matrix with the integer lines that the jingcai product uses.

Usage:
    python data_pipeline/eval_user_pipeline.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(REPO_ROOT, "data_pipeline")
sys.path.insert(0, PIPELINE)

import eval_lambda_baselines as lam          # noqa: E402
import eval_market_baseline as market        # noqa: E402

DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "user_pipeline_evaluation.json")
COMPARISON = os.path.join(REPO_ROOT, "model_validation", "lambda_baseline_comparison.json")
WINDOW = 5
MIN_HISTORY = 3


def log(message: str) -> None:
    print(message, flush=True)


def build(match_key: str, players: dict, previous_xi: dict) -> None:
    pass


def score_1x2(probabilities: dict[str, float], actual: str) -> tuple[int, float, float]:
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
    connection.row_factory = sqlite3.Row

    starters: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in connection.execute("""
            SELECT match_key, team, player FROM dsl_players WHERE is_starting = 1"""):
        starters[(row["match_key"], row["team"])].add(row["player"])

    matches = [dict(r) for r in connection.execute("""
        SELECT match_key, league_code, match_date, home_team, away_team, score
        FROM dsl_matches WHERE score LIKE '%-%' ORDER BY match_date""")]

    xg = defaultdict(float)
    for row in connection.execute("SELECT match_key, team, xg FROM dsl_shots"):
        try:
            xg[(row["match_key"], row["team"])] += float(row["xg"])
        except (TypeError, ValueError):
            continue

    history = defaultdict(list)
    last_date: dict[str, str] = {}
    last_xi: dict[str, set[str]] = {}
    records: dict[str, dict] = {}

    for match in matches:
        home, away, key = match["home_team"], match["away_team"], match["match_key"]
        fh, fa = (int(x) for x in match["score"].split("-"))
        hx, ax = history[home], history[away]
        if len(hx) >= MIN_HISTORY and len(ax) >= MIN_HISTORY:
            hgf, hga = lam.decayed(hx, 0.0, WINDOW)
            agf, aga = lam.decayed(ax, 0.0, WINDOW)
            lh, la = lam.lambdas(hgf, hga, agf, aga)
            grid = lam.matrix(lh, la)
            probs = lam.outcome_probs(grid)

            home_xi = starters.get((key, home), set())
            away_xi = starters.get((key, away), set())
            home_cont = len(home_xi & last_xi.get(home, set())) / max(1, len(home_xi)) if home_xi else None
            away_cont = len(away_xi & last_xi.get(away, set())) / max(1, len(away_xi)) if away_xi else None

            def rest_days(team: str, today: str) -> float | None:
                before = last_date.get(team)
                if not before:
                    return None
                return (date.fromisoformat(today) - date.fromisoformat(before)).days

            records[key] = {"probs": probs, "grid": grid, "lh": lh, "la": la,
                            "actual": "H" if fh > fa else ("D" if fh == fa else "A"),
                            "match_date": match["match_date"], "league": match["league_code"],
                            "home_cont": home_cont, "away_cont": away_cont,
                            "rest_home": rest_days(home, match["match_date"]),
                            "rest_away": rest_days(away, match["match_date"]),
                            "fh": fh, "fa": fa}

        history[home].insert(0, (xg.get((key, home), 0.0), xg.get((key, away), 0.0)))
        history[away].insert(0, (xg.get((key, away), 0.0), xg.get((key, home), 0.0)))
        last_date[home] = match["match_date"]
        last_date[away] = match["match_date"]
        if (key, home) in starters:
            last_xi[home] = starters[(key, home)]
        if (key, away) in starters:
            last_xi[away] = starters[(key, away)]

    # market 1X2
    cutoff = None
    if os.path.exists(COMPARISON):
        cutoff = json.load(open(COMPARISON, encoding="utf-8")).get("settings", {}).get("holdout_start")
    by_league_date = defaultdict(list)
    for match in matches:
        by_league_date[(match["league_code"], match["match_date"])].append(match)
    market_probs: dict[str, dict[str, float]] = {}
    for code, league in market.FEEDS.items():
        text = market.fetch_csv(code)
        if not text:
            continue
        for row in market.parse_feed(code, text):
            if "b365" not in row:
                continue
            best, best_score = None, 0.0
            for delta in (0, 1, -1):
                day = (date.fromisoformat(row["date"]) + timedelta(days=delta)).isoformat()
                for candidate in by_league_date.get((league, day), []):
                    value = min(market.similarity(row["home"], candidate["home_team"]),
                                market.similarity(row["away"], candidate["away_team"]))
                    if value > best_score:
                        best, best_score = candidate, value
            if best and best_score >= 0.55 and best["match_key"] in records:
                market_probs[best["match_key"]] = {k: row["b365"][k] for k in ("H", "D", "A")}

    lines: dict[str, float] = {}
    for code, league in market.FEEDS.items():
        text = market.fetch_csv(code)
        if not text:
            continue
        for row in market.parse_ah_feed(text):
            best, best_score = None, 0.0
            for delta in (0, 1, -1):
                day = (date.fromisoformat(row["date"]) + timedelta(days=delta)).isoformat()
                for candidate in by_league_date.get((league, day), []):
                    value = min(market.similarity(row["home"], candidate["home_team"]),
                                market.similarity(row["away"], candidate["away_team"]))
                    if value > best_score:
                        best, best_score = candidate, value
            if best and best_score >= 0.55:
                lines[best["match_key"]] = row["line"]

    pairs = [k for k in records if k in market_probs]
    train = [k for k in pairs if cutoff and records[k]["match_date"] <= cutoff]
    holdout = [k for k in pairs if cutoff and records[k]["match_date"] > cutoff]
    log(f"records={len(records)} with market={len(pairs)} train={len(train)} holdout={len(holdout)}")

    def blend(model: dict[str, float], prior: dict[str, float], weight: float) -> dict[str, float]:
        mixed = {k: (1 - weight) * model[k] + weight * prior[k] for k in ("H", "D", "A")}
        total = sum(mixed.values())
        return {k: v / total for k, v in mixed.items()}

    best_w, best_brier = 0.0, float("inf")
    for step in range(21):
        w = step / 20
        total = sum(score_1x2(blend(records[k]["probs"], market_probs[k], w), records[k]["actual"])[1]
                    for k in train)
        if total / len(train) < best_brier:
            best_brier, best_w = total / len(train), w
    log(f"blend weight fitted on train: {best_w:.2f} (train brier {best_brier:.4f})")

    # squad adjustment (leaky, upper bound): lambda scaled by XI continuity
    best_k, best_kb = 0.0, float("inf")
    for step in range(-10, 11):
        k = step / 100
        total = 0.0
        for key in train:
            record = records[key]
            lh = record["lh"] * math.exp(k * ((record["home_cont"] or 0.8) - 0.8))
            la = record["la"] * math.exp(k * ((record["away_cont"] or 0.8) - 0.8))
            total += score_1x2(lam.outcome_probs(lam.matrix(lh, la)), record["actual"])[1]
        if total / len(train) < best_kb:
            best_kb, best_k = total / len(train), k
    log(f"squad coefficient fitted on train (leaky): {best_k:+.2f} (train brier {best_kb:.4f})")

    variant_rows: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    handicap_rows: dict[str, list] = defaultdict(list)
    for key in holdout:
        record = records[key]
        actual = record["actual"]
        variant_rows["model_xg"].append(score_1x2(record["probs"], actual))
        prior = market_probs[key]
        variant_rows["market"].append(score_1x2(prior, actual))
        variant_rows["blend"].append(score_1x2(blend(record["probs"], prior, best_w), actual))
        lh = record["lh"] * math.exp(best_k * ((record["home_cont"] or 0.8) - 0.8))
        la = record["la"] * math.exp(best_k * ((record["away_cont"] or 0.8) - 0.8))
        squad_probs = lam.outcome_probs(lam.matrix(lh, la))
        variant_rows["blend_squad"].append(
            score_1x2(blend(squad_probs, prior, best_w), actual))

        # competitive-lottery handicap three-way from the score matrix, integer lines only
        grid = lam.matrix(lh, la)
        line = lines.get(key)
        if line is not None and abs(line - round(line)) < 1e-9:
            win = draw = lose = 0.0
            for i in range(len(grid)):
                for j in range(len(grid)):
                    margin = i - j + line
                    if margin > 0:
                        win += grid[i][j]
                    elif margin == 0:
                        draw += grid[i][j]
                    else:
                        lose += grid[i][j]
            p3 = {"让胜": win, "让平": draw, "让负": lose}
            goals_home = int(record.get("fh", 0))
            actual_margin = (record["fh"] - record["fa"]) + line
            wanted = "让胜" if actual_margin > 0 else ("让平" if actual_margin == 0 else "让负")
            y = {k: 0.0 for k in p3}
            y[wanted] = 1.0
            correct = int(max(p3, key=p3.get) == wanted)
            brier3 = sum((p3[k] - y[k]) ** 2 for k in p3)
            logloss3 = -math.log(max(1e-12, p3[wanted]))
            handicap_rows["model_3way"].append((correct, brier3, logloss3))
            if p3["让平"] > 0.25:
                handicap_rows["model_3way_draw_ge_25"].append((correct, brier3, logloss3))

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "Component-wise evaluation of the described pipeline: recent xG lambda, market "
            "prior, fitted blend, and a deliberately leaky squad-continuity upper bound. "
            "Weight and coefficient are fitted on the training segment only."),
        "window": WINDOW,
        "holdout_start": cutoff,
        "fitted": {"blend_weight": best_w, "squad_coefficient": best_k,
                   "note": "squad coefficient is leaky by construction"},
        "coverage": {"records": len(records), "with_market": len(pairs),
                     "train": len(train), "holdout": len(holdout)},
        "metrics": {name: summarise(rows) for name, rows in variant_rows.items()},
        "handicap_three_way": {name: summarise(rows) for name, rows in handicap_rows.items()},
        "blend_weight_curve": {round(step / 10, 1): round(sum(
            score_1x2(blend(records[k]["probs"], market_probs[k], step / 10),
                      records[k]["actual"])[1] for k in holdout) / len(holdout), 4)
            for step in range(11)},
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for name in ("model_xg", "market", "blend", "blend_squad"):
        m = payload["metrics"].get(name)
        if m and m.get("n"):
            log(f"  {name:12s} n={m['n']:4d} acc={m['accuracy']:.3f} "
                f"brier={m['brier']:.4f} logloss={m['logloss']:.4f}")
    for name in ("model_3way", "model_3way_draw_ge_25"):
        m = payload["handicap_three_way"].get(name)
        if m and m.get("n"):
            log(f"  {name:22s} n={m['n']:4d} acc={m['accuracy']:.3f} "
                f"brier3={m['brier']:.4f} logloss3={m['logloss']:.4f}")
    log("  blend weight curve (holdout brier): "
        + ", ".join(f"{w}={b:.4f}" for w, b in payload["blend_weight_curve"].items()))
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
