"""V5.2 market-anchored score distribution candidate.

Sequence: de-vig Over/Under -> infer total-goals lambda -> split total lambda
to best fit de-vigged 1X2 -> rank scorelines from joint probabilities.

This module is versioned in the repository but intentionally does not replace
V5.1 or claim production activation. Only use verified timestamped pre-kickoff
decimal odds. Supports half-goal O/U lines (2.5, 3.5), not quarter lines.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple


def _check_odds(*odds: float) -> None:
    if not all(math.isfinite(x) and x > 1.0 for x in odds):
        raise ValueError("Decimal odds must be finite and greater than 1")


def devig_two_way(over_odds: float, under_odds: float) -> Tuple[float, float]:
    _check_odds(over_odds, under_odds)
    over, under = 1.0 / over_odds, 1.0 / under_odds
    total = over + under
    return over / total, under / total


def devig_1x2(home_odds: float, draw_odds: float, away_odds: float) -> Tuple[float, float, float]:
    _check_odds(home_odds, draw_odds, away_odds)
    raw = (1.0 / home_odds, 1.0 / draw_odds, 1.0 / away_odds)
    total = sum(raw)
    return tuple(x / total for x in raw)


def poisson_pmf(lam: float, max_goals: int = 15) -> List[float]:
    if not math.isfinite(lam) or lam < 0 or max_goals < 0:
        raise ValueError("lambda must be finite and non-negative; max_goals must be >= 0")
    p = math.exp(-lam)
    values = [p]
    for k in range(1, max_goals + 1):
        p *= lam / k
        values.append(p)
    return values


def poisson_over_probability(total_lambda: float, line: float, max_goals: int = 40) -> float:
    if not math.isfinite(line) or abs((line - 0.5) - round(line - 0.5)) > 1e-9:
        raise ValueError("Only half-goal lines such as 2.5 or 3.5 are supported")
    threshold = math.floor(line) + 1
    return min(1.0, sum(poisson_pmf(total_lambda, max_goals)[threshold:]))


def infer_total_lambda(p_over: float, line: float, lo: float = 0.01, hi: float = 10.0) -> float:
    if not math.isfinite(p_over) or not 0 < p_over < 1:
        raise ValueError("p_over must be strictly between 0 and 1")
    if lo <= 0 or hi <= lo:
        raise ValueError("Invalid lambda search interval")
    if poisson_over_probability(lo, line) > p_over or poisson_over_probability(hi, line) < p_over:
        raise ValueError("Market probability outside lambda search bounds")
    left, right = lo, hi
    for _ in range(100):
        mid = (left + right) / 2
        if poisson_over_probability(mid, line) < p_over:
            left = mid
        else:
            right = mid
    return (left + right) / 2


def score_grid(lambda_home: float, lambda_away: float, max_goals: int = 15) -> Dict[str, object]:
    hp, ap = poisson_pmf(lambda_home, max_goals), poisson_pmf(lambda_away, max_goals)
    scores, bins = [], {"0-1": 0.0, "2": 0.0, "3-4": 0.0, "5-6": 0.0, "7+": 0.0}
    p_home = p_draw = p_away = 0.0
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            p = ph * pa
            scores.append((p, h, a))
            total = h + a
            if total <= 1:
                bins["0-1"] += p
            elif total == 2:
                bins["2"] += p
            elif total <= 4:
                bins["3-4"] += p
            elif total <= 6:
                bins["5-6"] += p
            else:
                bins["7+"] += p
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
    scores.sort(reverse=True)
    mass = sum(hp) * sum(ap)
    return {
        "top_scores": [{"home": h, "away": a, "probability": p} for p, h, a in scores[:2]],
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "goal_bins": bins, "probability_mass": mass,
        "omitted_tail_probability": max(0.0, 1.0 - mass),
    }


def allocate_lambdas(total_lambda: float, p_home: float, p_draw: float, p_away: float,
                     steps: int = 1001, max_goals: int = 15) -> Dict[str, object]:
    targets = (p_home, p_draw, p_away)
    if any(not math.isfinite(p) or p < 0 or p > 1 for p in targets) or abs(sum(targets) - 1) > 1e-6:
        raise ValueError("1X2 probabilities must be in [0,1] and sum to 1")
    if not math.isfinite(total_lambda) or total_lambda <= 0 or steps < 2:
        raise ValueError("total_lambda must be positive and steps must be >= 2")
    best = None
    for i in range(steps):
        lh = total_lambda * i / (steps - 1)
        la = total_lambda - lh
        grid = score_grid(lh, la, max_goals)
        error = sum((grid[k] - target) ** 2 for k, target in zip(("p_home", "p_draw", "p_away"), targets))
        if best is None or error < best[0]:
            best = (error, lh, la, grid)
    error, lh, la, grid = best
    return {
        "lambda_home": lh, "lambda_away": la, "lambda_total": total_lambda,
        "market_1x2_fit_squared_error": error, "distribution": grid,
        "version": "V5.2-market-lambda-candidate", "production_v5_1_changed": False,
    }


def predict_from_markets(*, over_odds: float, under_odds: float, goal_line: float,
                         home_odds: float, draw_odds: float, away_odds: float,
                         max_goals: int = 15) -> Dict[str, object]:
    p_over, p_under = devig_two_way(over_odds, under_odds)
    total_lambda = infer_total_lambda(p_over, goal_line)
    p_home, p_draw, p_away = devig_1x2(home_odds, draw_odds, away_odds)
    result = allocate_lambdas(total_lambda, p_home, p_draw, p_away, max_goals=max_goals)
    result["market"] = {
        "goal_line": goal_line, "over_odds": over_odds, "under_odds": under_odds,
        "p_over_devig": p_over, "p_under_devig": p_under,
        "p_home_devig": p_home, "p_draw_devig": p_draw, "p_away_devig": p_away,
    }
    result["method"] = "O/U total lambda first, then home/away lambda allocation"
    result["requires_verified_timestamped_pre_kickoff_odds"] = True
    return result
