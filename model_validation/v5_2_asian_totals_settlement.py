"""Asian totals settlement and probability decomposition for half/quarter lines.

Quarter lines split stake evenly across the adjacent half lines:
2.25 = 50% on 2.0 + 50% on 2.5; 2.75 = 50% on 2.5 + 50% on 3.0.
Probabilities are derived from a supplied total-goals PMF or scoreline grid.
This module does not fetch or invent historical odds/results.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


def _valid_odds(odds: float) -> None:
    if not math.isfinite(float(odds)) or float(odds) <= 1:
        raise ValueError("decimal odds must be finite and greater than 1")


def _components(line: float) -> Tuple[float, ...]:
    if not math.isfinite(line) or line < 0:
        raise ValueError("line must be finite and non-negative")
    quarter = round(line * 4) / 4
    if abs(quarter - line) > 1e-9:
        raise ValueError("line must be in quarter-goal increments")
    frac = round((line - math.floor(line)) * 100) / 100
    if abs(frac - 0.25) < 1e-9:
        return (math.floor(line), math.floor(line) + 0.5)
    if abs(frac - 0.75) < 1e-9:
        return (math.floor(line) + 0.5, math.floor(line) + 1.0)
    if abs(frac) < 1e-9 or abs(frac - 0.5) < 1e-9:
        return (line,)
    raise ValueError("unsupported line fraction")


def _single_line_profit(total: int, line: float, side: str, odds: float) -> float:
    if abs(line - round(line)) < 1e-9:
        if side == "over":
            return odds - 1 if total > line else 0.0 if total == line else -1.0
        return odds - 1 if total < line else 0.0 if total == line else -1.0
    if side == "over":
        return odds - 1 if total > line else -1.0
    return odds - 1 if total < line else -1.0


def settlement_profit(total_goals: int, line: float, side: str, odds: float) -> float:
    """Net profit per 1 unit stake; +odds-1 win, -1 loss, 0 push."""
    if not isinstance(total_goals, int) or total_goals < 0:
        raise ValueError("total_goals must be a non-negative integer")
    if side not in {"over", "under"}:
        raise ValueError("side must be 'over' or 'under'")
    _valid_odds(odds)
    parts = _components(float(line))
    return sum(_single_line_profit(total_goals, part, side, odds) for part in parts) / len(parts)


def _label(profit: float, odds: float) -> str:
    if profit <= -1 + 1e-9:
        return "loss"
    if profit < -1e-9:
        return "half_loss"
    if abs(profit) <= 1e-9:
        return "push"
    if profit < odds - 1 - 1e-9:
        return "half_win"
    return "win"


def settle_probabilities(
    *, line: float, side: str, odds: float,
    total_goal_probabilities: Mapping[int, float],
) -> Dict[str, object]:
    """Return settlement probabilities and expected return per unit stake."""
    if side not in {"over", "under"}:
        raise ValueError("side must be 'over' or 'under'")
    _valid_odds(odds)
    _components(float(line))
    probs = {int(k): float(v) for k, v in total_goal_probabilities.items()}
    if not probs or any(k < 0 or not math.isfinite(v) or v < 0 for k, v in probs.items()):
        raise ValueError("total-goal probabilities must be non-negative and finite")
    mass = sum(probs.values())
    if abs(mass - 1.0) > 1e-6:
        raise ValueError("total-goal probabilities must sum to 1 within 1e-6")
    categories = {k: 0.0 for k in ("win", "half_win", "push", "half_loss", "loss")}
    expected_profit = 0.0
    for total, probability in probs.items():
        profit = settlement_profit(total, line, side, odds)
        categories[_label(profit, odds)] += probability
        expected_profit += probability * profit
    return {
        "line": line,
        "side": side,
        "decimal_odds": odds,
        "settlement_probabilities": categories,
        "win_probability_including_half_win": categories["win"] + categories["half_win"],
        "loss_probability_including_half_loss": categories["loss"] + categories["half_loss"],
        "push_probability": categories["push"],
        "expected_net_return_per_unit_stake": expected_profit,
        "probability_mass": mass,
        "settlement_rule": "quarter line splits stake equally across adjacent lines",
    }


def poisson_total_pmf(lambda_total: float, max_goals: int = 60) -> Dict[int, float]:
    if not math.isfinite(lambda_total) or lambda_total < 0 or max_goals < 1:
        raise ValueError("lambda_total must be non-negative and max_goals >= 1")
    vals = [math.exp(-lambda_total)]
    for k in range(1, max_goals + 1):
        vals.append(vals[-1] * lambda_total / k)
    mass = sum(vals)
    # Preserve the probability mass in the modeled support; normalize to avoid
    # a truncated distribution causing quarter-line settlement probabilities < 1.
    return {k: p / mass for k, p in enumerate(vals)}
