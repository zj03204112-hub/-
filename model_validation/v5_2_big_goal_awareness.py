"""V5.2 big-goal awareness and score-tail reporting layer.

Experimental and isolated from formal V5.1. Combines verified opening/current
O/U line and prices with model-implied expected goals, and explicitly surfaces
high-total scorelines (e.g. 4-2, 3-2, 5-1, 5-2, 4-3) without falsely ranking them
as the two most likely scores. All odds must be decimal odds captured pre-kickoff.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


def _valid_odds(*values: float) -> None:
    if not all(math.isfinite(float(v)) and float(v) > 1.0 for v in values):
        raise ValueError("decimal odds must be finite and greater than 1")


def devig(over_odds: float, under_odds: float) -> Tuple[float, float]:
    _valid_odds(over_odds, under_odds)
    o, u = 1.0 / over_odds, 1.0 / under_odds
    return o / (o + u), u / (o + u)


def _line_is_half(line: float) -> bool:
    return math.isfinite(line) and abs((line - 0.5) - round(line - 0.5)) < 1e-9


def assess_big_goal_sensitivity(
    *,
    current_line: float,
    current_over_odds: float,
    current_under_odds: float,
    lambda_home: float,
    lambda_away: float,
    opening_line: Optional[float] = None,
    opening_over_odds: Optional[float] = None,
    opening_under_odds: Optional[float] = None,
    over_price_move_pct: Optional[float] = None,
    under_price_move_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute transparent market/model big-goal sensitivity (0-100).

    Sensitivity is a signal score, not the probability of an over bet winning.
    Probability is separately reported from a Poisson total-goals model.
    Quarter lines are not reduced to a binary win probability here.
    """
    _valid_odds(current_over_odds, current_under_odds)
    if not (math.isfinite(lambda_home) and math.isfinite(lambda_away)
            and lambda_home >= 0 and lambda_away >= 0):
        raise ValueError("lambdas must be finite and non-negative")
    if not _line_is_half(current_line):
        raise ValueError("current_line must be a half-goal line such as 2.5 or 3.5")
    if (opening_line is None) != (opening_over_odds is None or opening_under_odds is None):
        raise ValueError("opening line and both opening odds must be supplied together")
    if opening_line is not None:
        if not _line_is_half(opening_line):
            raise ValueError("opening_line must be a half-goal line")
        _valid_odds(float(opening_over_odds), float(opening_under_odds))

    p_over, p_under = devig(current_over_odds, current_under_odds)
    total_lam = lambda_home + lambda_away
    # P(total > half-goal line), with a sufficiently wide Poisson support.
    pmf = [math.exp(-total_lam)]
    for k in range(1, 61):
        pmf.append(pmf[-1] * total_lam / k)
    over_prob = sum(pmf[int(math.floor(current_line)) + 1:])
    market_edge_pp = (p_over - 0.5) * 100.0
    model_edge_pp = (over_prob - 0.5) * 100.0

    line_move = 0.0 if opening_line is None else current_line - float(opening_line)
    if opening_line is not None:
        open_over, open_under = devig(float(opening_over_odds), float(opening_under_odds))
        price_signal_pp = (p_over - open_over) * 100.0
    else:
        price_signal_pp = 0.0
    if over_price_move_pct is not None:
        price_signal_pp += max(-10.0, min(10.0, float(over_price_move_pct))) * 0.25
    if under_price_move_pct is not None:
        price_signal_pp -= max(-10.0, min(10.0, float(under_price_move_pct))) * 0.25

    # Bounded heuristic score; explicitly not a calibrated win probability.
    raw = 50.0 + 1.1 * model_edge_pp + 0.65 * market_edge_pp + 0.8 * price_signal_pp + 8.0 * line_move
    sensitivity = round(max(0.0, min(100.0, raw)), 1)
    if sensitivity >= 65:
        tier = "高"
    elif sensitivity >= 55:
        tier = "偏高"
    elif sensitivity <= 35:
        tier = "偏低"
    elif sensitivity <= 45:
        tier = "低"
    else:
        tier = "中性"

    return {
        "module_version": "V5.2-big-goal-awareness-v1",
        "big_goal_sensitivity": sensitivity,
        "sensitivity_tier": tier,
        "p_over_current_line_model": over_prob,
        "p_under_current_line_model": max(0.0, 1.0 - over_prob),
        "p_over_current_line_market_devig": p_over,
        "p_under_current_line_market_devig": p_under,
        "lambda_home": lambda_home,
        "lambda_away": lambda_away,
        "lambda_total": total_lam,
        "opening_line": opening_line,
        "current_line": current_line,
        "line_move": line_move,
        "over_price_move_pct_input": over_price_move_pct,
        "under_price_move_pct_input": under_price_move_pct,
        "price_signal_percentage_points": price_signal_pp,
        "definition": "Sensitivity score is a market/model signal, not a calibrated over probability.",
        "validation_status": "EXPERIMENTAL_NOT_BACKTESTED",
    }


def high_total_score_candidates(
    score_probabilities: Iterable[Tuple[int, int, float]],
    *,
    min_total_goals: int = 5,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """Surface likely scorelines in the high-total tail as a separate list."""
    if min_total_goals < 1 or limit < 1:
        raise ValueError("min_total_goals and limit must be positive")
    rows = []
    for home, away, probability in score_probabilities:
        if (not isinstance(home, int) or not isinstance(away, int)
                or home < 0 or away < 0
                or not math.isfinite(probability) or probability < 0):
            raise ValueError("invalid scoreline probability row")
        total = home + away
        if total >= min_total_goals:
            rows.append({
                "home": home,
                "away": away,
                "score": f"{home}-{away}",
                "total_goals": total,
                "probability": probability,
                "tail_candidate": True,
            })
    rows.sort(key=lambda row: row["probability"], reverse=True)
    return rows[:limit]
