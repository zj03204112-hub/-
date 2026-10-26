"""Market-anchored goal distribution and score allocation (shadow-only).

Workflow:
1) De-vig Over/Under odds at an integer/half-goal line.
2) Infer total-goals Poisson lambda matching the market over probability.
3) Allocate that total lambda between home and away by fitting de-vigged 1X2
   probabilities with a constrained independent-Poisson score grid.
4) Return calibrated score probabilities and tail bins.

This is an experiment only. Use only timestamped, authentic pre-kickoff odds.
It does not modify or call production V5.1.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

EPS = 1e-12


def _valid_odds(odds: float) -> bool:
    return math.isfinite(odds) and odds > 1.0


def devig_two_way(over_odds: float, under_odds: float) -> Tuple[float, float]:
    """Normalize decimal odds to over/under probabilities, removing 2-way margin."""
    if not _valid_odds(over_odds) or not _valid_odds(under_odds):
        raise ValueError("Decimal odds must be finite and greater than 1")
    raw_over, raw_under = 1.0 / over_odds, 1.0 / under_odds
    total = raw_over + raw_under
    return raw_over / total, raw_under / total


def devig_1x2(home_odds: float, draw_odds: float, away_odds: float) -> Tuple[float, float, float]:
    """Normalize decimal 1X2 odds to probabilities, removing 3-way margin."""
    odds = (home_odds, draw_odds, away_odds)
    if not all(_valid_odds(x) for x in odds):
        raise ValueError("All decimal 1X2 odds must be finite and greater than 1")
    raw = tuple(1.0 / x for x in odds)
    total = sum(raw)
    return tuple(x / total for x in raw)


def poisson_pmf(lam: float, max_goals: int = 15) -> List[float]:
    if not math.isfinite(lam) or lam < 0:
        raise ValueError("lambda must be finite and non-negative")
    if max_goals < 0:
        raise ValueError("max_goals must be non-negative")
    p = math.exp(-lam)
    out = [p]
    for k in range(1, max_goals + 1):
        p *= lam / k
        out.append(p)
    return out


def poisson_over_probability(total_lambda: float, line: float, max_goals: int = 30) -> float:
    """Probability of winning Over at a half-goal line (e.g. 2.5, 3.5)."""
    if not math.isfinite(line) or abs((line - 0.5) - round(line - 0.5)) > 1e-9:
        raise ValueError("Only half-goal lines (e.g. 2.5, 3.5) are supported")
    threshold = int(math.floor(line)) + 1
    pmf = poisson_pmf(total_lambda, max_goals)
    return min(1.0, sum(pmf[threshold:]))


def infer_total_lambda(over_probability: float, line: float, *, lo: float = 0.01,
                       hi: float = 10.0, iterations: int = 100) -> float:
    """Find total Poisson lambda whose Over probability matches the de-vig market."""
    if not math.isfinite(over_probability) or not 0 < over_probability < 1:
        raise ValueError("over_probability must be strictly between 0 and 1")
    if not math.isfinite(lo) or not math.isfinite(hi) or lo <= 0 or hi <= lo:
        raise ValueError("Invalid lambda search bounds")
    if poisson_over_probability(lo, line) > over_probability or poisson_over_probability(hi, line) < over_probability:
        raise ValueError("Market probability is outside configured lambda search bounds")
    left, right = lo, hi
    for _ in range(iterations):
        mid = (left + right) / 2
        if poisson_over_probability(mid, line) < over_probability:
            left = mid
        else:
            right = mid
    return (left + right) / 2


def score_grid(lambda_home: float, lambda_away: float, max_goals: int = 15) -> Dict[str, object]:
    home, away = poisson_pmf(lambda_home, max_goals), poisson_pmf(lambda_away, max_goals)
    grid = []
    hwin = draw = awin = 0.0
    total_bins = {"0-1": 0.0, "2": 0.0, "3-4": 0.0, "5-6": 0.0, "7+": 0.0}
    total_pmf = [0.0] * (2 * max_goals + 1)
    for h, ph in enumerate(home):
        for a, pa in enumerate(away):
            p = ph * pa
            grid.append((p, h, a))
            total = h + a
            total_pmf[total] += p
            if total <= 1:
                total_bins["0-1"] += p
            elif total == 2:
                total_bins["2"] += p
            elif total <= 4:
                total_bins["3-4"] += p
            elif total <= 6:
                total_bins["5-6"] += p
            else:
                total_bins["7+"] += p
            if h > a:
                hwin += p
            elif h == a:
                draw += p
            else:
                awin += p
    grid.sort(reverse=True)
    mass = sum(home) * sum(away)
    return {
        "top_scores": [{"home": h, "away": a, "probability": p} for p, h, a in grid[:2]],
        "p_home": hwin, "p_draw": draw, "p_away": awin,
        "total_goals_pmf": total_pmf, "goal_bins": total_bins,
        "probability_mass": mass, "omitted_tail_probability": max(0.0, 1.0 - mass),
    }


def allocate_lambdas(total_lambda: float, p_home: float, p_draw: float, p_away: float,
                     *, max_goals: int = 15, steps: int = 1001) -> Dict[str, object]:
    """Split fixed total lambda by minimizing squared error to market 1X2 probabilities."""
    probs = (p_home, p_draw, p_away)
    if not all(math.isfinite(p) and 0 <= p <= 1 for p in probs):
        raise ValueError("1X2 probabilities must be finite and in [0,1]")
    if abs(sum(probs) - 1.0) > 1e-6:
        raise ValueError("1X2 probabilities must sum to 1")
    if not math.isfinite(total_lambda) or total_lambda <= 0:
        raise ValueError("total_lambda must be positive and finite")
    if steps < 2:
        raise ValueError("steps must be at least 2")

    best = None
    for i in range(steps):
        lh = total_lambda * i / (steps - 1)
        la = total_lambda - lh
        result = score_grid(lh, la, max_goals)
        err = sum((result[k] - target) ** 2 for k, target in
                  (("p_home", p_home), ("p_draw", p_draw), ("p_away", p_away)))
        if best is None or err < best[0]:
            best = (err, lh, la, result)
    _, lh, la, result = best
    return {
        "lambda_home": lh, "lambda_away": la, "lambda_total": total_lambda,
        "market_1x2_fit_squared_error": best[0], "distribution": result,
        "mode": "shadow_only", "production_v5_1_changed": False,
    }


def market_anchored_prediction(*, over_odds: float, under_odds: float, goal_line: float,
                               home_odds: float, draw_odds: float, away_odds: float,
                               max_goals: int = 15) -> Dict[str, object]:
    """Estimate total lambda from O/U odds, then split it using 1X2 odds."""
    p_over, p_under = devig_two_way(over_odds, under_odds)
    total_lambda = infer_total_lambda(p_over, goal_line)
    p_home, p_draw, p_away = devig_1x2(home_odds, draw_odds, away_odds)
    allocated = allocate_lambdas(total_lambda, p_home, p_draw, p_away, max_goals=max_goals)
    return {
        "market": {
            "goal_line": goal_line, "over_odds": over_odds, "under_odds": under_odds,
            "p_over_devig": p_over, "p_under_devig": p_under,
            "p_home_devig": p_home, "p_draw_devig": p_draw, "p_away_devig": p_away,
        },
        **allocated,
        "method": "O/U-derived total lambda then constrained 1X2 fit",
        "requires_verified_pre_kickoff_odds": True,
    }
