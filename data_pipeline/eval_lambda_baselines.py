#!/usr/bin/env python3
"""First-pass evaluation: does xG-based lambda beat goals-based lambda?

Self-contained trial on the detail layer (dsl_* tables) because those matches are newer
than anything in the legacy matches/results tables.

Design:
  * histories are built strictly from matches dated before the match being predicted
    (a conservative stand-in for the T-12h cutoff)
  * lambda uses the same linear form as compare_models.py so the comparison isolates
    one change only: goals-for/against rates become xG-for/against rates
  * 8x8 score matrix with the same Dixon-Coles low-score correction, normalised
  * chronological holdout: the last 30% of eligible matches, never used for anything else
  * three models: league-average Poisson, goals-based lambda, xG-based lambda

Metrics: H/D/A accuracy, Brier, Log Loss. No hyper-parameter is fitted on the holdout.

Usage:
    python data_pipeline/eval_lambda_baselines.py
"""

from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation",
                   os.getenv("LAMBDA_OUT", "lambda_baseline_comparison.json"))
N = 8
RHO = -0.05
MIN_HISTORY = 3
WINDOW_SIZE = int(os.getenv("LAMBDA_WINDOW", "30"))
WINDOW_DECAY = float(os.getenv("LAMBDA_DECAY", "0.12"))


def log(message: str) -> None:
    print(message, flush=True)


def poisson_pmf(lam: float, n: int = N) -> list[float]:
    values = [math.exp(-lam)]
    for k in range(1, n):
        values.append(values[-1] * lam / k)
    return values


def tau(i: int, j: int, lh: float, la: float) -> float:
    if i == 0 and j == 0:
        return 1 - lh * la * RHO
    if i == 0 and j == 1:
        return 1 + lh * RHO
    if i == 1 and j == 0:
        return 1 + la * RHO
    if i == 1 and j == 1:
        return 1 - RHO
    return 1.0


def matrix(lh: float, la: float) -> list[list[float]]:
    hp, ap = poisson_pmf(lh), poisson_pmf(la)
    grid = [[hp[i] * ap[j] * tau(i, j, lh, la) for j in range(N)] for i in range(N)]
    total = sum(map(sum, grid))
    return [[v / total for v in row] for row in grid]


def outcome_probs(grid: list[list[float]]) -> dict[str, float]:
    home = sum(grid[i][j] for i in range(N) for j in range(N) if i > j)
    draw = sum(grid[i][j] for i in range(N) for j in range(N) if i == j)
    away = sum(grid[i][j] for i in range(N) for j in range(N) if i < j)
    return {"H": home, "D": draw, "A": away}


def decayed(history: list[tuple[float, float]], rate: float = 0.12,
            window: int = 30) -> tuple[float, float]:
    """Newest-first history, truncated to `window` matches before weighting."""
    sample = history[:window] if window > 0 else history
    if not sample:
        return 0.0, 0.0
    weights = [math.exp(-rate * i) for i in range(len(sample))]
    total = sum(weights)
    return (sum(h[0] * w for h, w in zip(sample, weights)) / total,
            sum(h[1] * w for h, w in zip(sample, weights)) / total)


def lambdas(attack_for: float, attack_against: float, defence_for: float,
            defence_against: float) -> tuple[float, float]:
    """Same linear form as compare_models.model_lambdas for the goals path."""
    lh = max(0.15, min(4.0, 0.65 + 0.58 * attack_for + 0.30 * defence_against))
    la = max(0.12, min(3.5, 0.58 + 0.58 * defence_for + 0.30 * attack_against))
    return lh, la


def score(p: dict[str, float], actual: str) -> tuple[int, float, float]:
    y = {k: 0.0 for k in p}
    y[actual] = 1.0
    correct = int(max(p, key=p.get) == actual)
    brier = sum((p[k] - y[k]) ** 2 for k in p)
    logloss = -math.log(max(1e-12, p[actual]))
    return correct, brier, logloss


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row

    matches = [dict(r) for r in connection.execute("""
        SELECT match_key, league_code, match_date, home_team, away_team, score
        FROM dsl_matches
        WHERE match_date IS NOT NULL AND score IS NOT NULL AND score LIKE '%-%'
        ORDER BY match_date""")]
    if not matches:
        log("no matches with a final score in dsl_matches")
        return 1

    xg: dict[tuple[str, str], float] = defaultdict(float)
    for row in connection.execute("""
            SELECT match_key, team, xg FROM dsl_shots
            WHERE xg IS NOT NULL AND xg <> ''"""):
        try:
            xg[(row["match_key"], row["team"])] += float(row["xg"])
        except (TypeError, ValueError):
            continue
    log(f"matches={len(matches)} shot-rows aggregated for {len(xg)} team-match pairs")

    league_goals: dict[str, list[float]] = defaultdict(list)
    history_goals: dict[str, list[tuple[float, float]]] = defaultdict(list)
    history_xg: dict[str, list[tuple[float, float]]] = defaultdict(list)
    records = []

    for match in matches:
        home, away = match["home_team"], match["away_team"]
        try:
            fh, fa = (int(x) for x in match["score"].split("-"))
        except ValueError:
            continue
        league = match["league_code"]
        # Only use history available before this match date.
        hg, ag = history_goals.get(home, []), history_goals.get(away, [])
        hx, ax = history_xg.get(home, []), history_xg.get(away, [])
        eligible_goals = len(hg) >= MIN_HISTORY and len(ag) >= MIN_HISTORY
        eligible_xg = len(hx) >= MIN_HISTORY and len(ax) >= MIN_HISTORY
        avg = league_goals[league]
        league_mean = sum(avg) / len(avg) if len(avg) >= 20 else None

        if eligible_goals:
            h_gf, h_ga = decayed(hg, WINDOW_DECAY, WINDOW_SIZE)
            a_gf, a_ga = decayed(ag, WINDOW_DECAY, WINDOW_SIZE)
            lh, la = lambdas(h_gf, h_ga, a_gf, a_ga)
            p = outcome_probs(matrix(lh, la))
            c, b, l = score(p, "H" if fh > fa else "D" if fh == fa else "A")
            records.append({"model": "goals_lambda", "league": league, "date": match["match_date"],
                            "match_key": match["match_key"], "probs": p, "correct": c,
                            "brier": b, "logloss": l, "lambda_home": lh, "lambda_away": la,
                            "history": len(hg)})

        if eligible_xg:
            h_gf, h_ga = decayed(hx, WINDOW_DECAY, WINDOW_SIZE)
            a_gf, a_ga = decayed(ax, WINDOW_DECAY, WINDOW_SIZE)
            lh, la = lambdas(h_gf, h_ga, a_gf, a_ga)
            p = outcome_probs(matrix(lh, la))
            c, b, l = score(p, "H" if fh > fa else "D" if fh == fa else "A")
            records.append({"model": "xg_lambda", "league": league, "date": match["match_date"],
                            "match_key": match["match_key"], "probs": p, "correct": c,
                            "brier": b, "logloss": l, "lambda_home": lh, "lambda_away": la,
                            "history": len(hx)})

        if league_mean:
            # League-average Poisson baseline, fixed home edge.
            lh, la = league_mean * 0.55, league_mean * 0.45
            p = outcome_probs(matrix(lh, la))
            c, b, l = score(p, "H" if fh > fa else "D" if fh == fa else "A")
            records.append({"model": "league_average", "league": league, "date": match["match_date"],
                            "match_key": match["match_key"], "probs": p, "correct": c,
                            "brier": b, "logloss": l, "lambda_home": lh, "lambda_away": la})

        # update histories after prediction
        history_goals[home].insert(0, (fh, fa))
        history_goals[away].insert(0, (fa, fh))
        hxg, axg = xg.get((match["match_key"], home)), xg.get((match["match_key"], away))
        if hxg is not None and axg is not None:
            history_xg[home].insert(0, (hxg, axg))
            history_xg[away].insert(0, (axg, hxg))
        league_goals[league].append(fh + fa)

    if not records:
        log("no eligible matches")
        return 1

    # Chronological holdout: last 30% of distinct match dates.
    dates = sorted({r["date"] for r in records})
    split = max(1, int(len(dates) * 0.70))
    cutoff_date = dates[split - 1]
    holdout = [r for r in records if r["date"] > cutoff_date]
    log(f"dates={len(dates)} cutoff={cutoff_date} holdout records={len(holdout)}")

    def summarise(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        n = len(rows)
        return {
            "n": n,
            "accuracy": round(sum(r["correct"] for r in rows) / n, 4),
            "brier": round(sum(r["brier"] for r in rows) / n, 4),
            "logloss": round(sum(r["logloss"] for r in rows) / n, 4),
            "mean_lambda_total": round(sum(r["lambda_home"] + r["lambda_away"] for r in rows) / n, 3),
        }

    train = [r for r in records if r["date"] <= cutoff_date]

    train_models = {}
    for name in ("league_average", "goals_lambda", "xg_lambda"):
        rows = [r for r in train if r["model"] == name]
        train_models[name] = summarise(rows)

    models = {}
    for name in ("league_average", "goals_lambda", "xg_lambda"):
        rows = [r for r in holdout if r["model"] == name]
        models[name] = summarise(rows)
        hist = sorted(r.get("history", 0) for r in rows)
        if hist:
            models[name]["history_median"] = hist[len(hist)//2]
            models[name]["history_max"] = hist[-1]
        models[name]["by_league"] = {
            league: summarise([r for r in rows if r["league"] == league])
            for league in sorted({r["league"] for r in rows})
        }

    # Pairwise comparison on matches where both lambda models are eligible.
    keys_by_model = defaultdict(set)
    for r in holdout:
        keys_by_model[r["model"]].add(r["match_key"])
    common = keys_by_model["goals_lambda"] & keys_by_model["xg_lambda"]
    pair = {"n": len(common)}
    for name in ("goals_lambda", "xg_lambda"):
        rows = [r for r in holdout if r["model"] == name and r["match_key"] in common]
        pair[name] = summarise(rows)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "Leakage-safe first-pass comparison on the dsl_* detail layer. Histories use only "
            "matches dated before the predicted match. Lambda keeps the compare_models.py linear "
            "form so the only difference between goals_lambda and xg_lambda is the input rate "
            "(goals vs xG). Chronological holdout: last 30% of match dates, no tuning on it."),
        "settings": {"matrix": f"{N}x{N} Poisson with Dixon-Coles rho={RHO}",
                     "decay": 0.12, "min_history": MIN_HISTORY,
                     "holdout_start": cutoff_date, "train_fraction": 0.70},
        "window_size": WINDOW_SIZE,
        "decay": WINDOW_DECAY,
        "train_models": train_models,
        "models": models,
        "pairwise_goals_vs_xg": pair,
        "caveats": [
            "xG history only exists from 2026-08, so early matches fall back to ineligible and "
            "are excluded from the xG arm rather than filled with zeros.",
            "The holdout is roughly one third of a three-month window; treat differences under "
            "0.005 Brier as noise.",
            "No market baseline yet: this trial only compares feature variants against each other.",
        ],
    }
    rows_csv = os.path.join(REPO_ROOT, "model_validation", "lambda_baseline_holdout.csv")
    with open(rows_csv, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["model", "league", "date", "match_key", "correct", "brier", "logloss",
                         "p_home", "p_draw", "p_away", "lambda_home", "lambda_away"])
        for r in sorted(holdout, key=lambda x: (x["model"], x["date"], x["match_key"])):
            writer.writerow([r["model"], r["league"], r["date"], r["match_key"], r["correct"],
                             f'{r["brier"]:.6f}', f'{r["logloss"]:.6f}',
                             f'{r["probs"]["H"]:.6f}', f'{r["probs"]["D"]:.6f}', f'{r["probs"]["A"]:.6f}',
                             f'{r["lambda_home"]:.4f}', f'{r["lambda_away"]:.4f}'])
    payload["holdout_csv"] = os.path.relpath(rows_csv, REPO_ROOT)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for name in ("league_average", "goals_lambda", "xg_lambda"):
        m = models[name]
        if m.get("n"):
            log(f"  {name:15s} n={m['n']:4d} acc={m['accuracy']:.3f} brier={m['brier']:.4f} logloss={m['logloss']:.4f}")
    log(f"  pairwise n={pair['n']}")
    for name in ("goals_lambda", "xg_lambda"):
        m = pair[name]
        if m.get("n"):
            log(f"    {name:13s} brier={m['brier']:.4f} logloss={m['logloss']:.4f} acc={m['accuracy']:.3f}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
