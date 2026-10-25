"""Shadow-only calibration utilities for V5.1 O/U tail experiment.

This module never changes production V5.1. Fit weights only on a chronological
training split, then apply unchanged weights to a later test split.
Pure Python; no third-party dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Sequence

TOTAL_GOAL_BINS = ((0, 1), (2, 2), (3, 4), (5, 6), (7, None))
NET_GOAL_BINS = ((0, 0), (1, 1), (2, 2), (3, None))


def _bin_index(value: int, bins: Sequence[tuple[int, int | None]]) -> int:
    if value < 0:
        raise ValueError("goal counts must be non-negative")
    for i, (lo, hi) in enumerate(bins):
        if value >= lo and (hi is None or value <= hi):
            return i
    raise ValueError(f"value {value} is not covered by bins")


def _normalise(values: Sequence[float]) -> list[float]:
    if not values or any(not isfinite(v) or v < 0 for v in values):
        raise ValueError("probabilities must be finite and non-negative")
    total = sum(values)
    if total <= 0:
        raise ValueError("probability mass must be positive")
    return [v / total for v in values]


@dataclass(frozen=True)
class BinCalibrator:
    bins: tuple[tuple[int, int | None], ...]
    weights: tuple[float, ...]
    training_rows: int
    smoothing: float

    def apply(self, pmf: Sequence[float]) -> list[float]:
        """Scale each outcome by its bin weight and renormalise."""
        p = _normalise(pmf)
        calibrated = [
            prob * self.weights[_bin_index(goals, self.bins)]
            for goals, prob in enumerate(p)
        ]
        return _normalise(calibrated)


def fit_bin_calibrator(
    predicted_pmfs: Iterable[Sequence[float]],
    actual_values: Iterable[int],
    bins: Sequence[tuple[int, int | None]],
    smoothing: float = 20.0,
) -> BinCalibrator:
    """Estimate shrunken observed/predicted mass ratios on training data only.

    predicted_pmfs[i][g] is the pre-match probability of exactly g goals.
    Smoothing pulls each bin's ratio toward 1, preventing unstable tail weights.
    """
    rows = list(predicted_pmfs)
    actual = list(actual_values)
    if len(rows) != len(actual) or not rows:
        raise ValueError("predictions and outcomes must be non-empty and aligned")
    if smoothing < 0:
        raise ValueError("smoothing must be non-negative")
    pred_mass = [0.0] * len(bins)
    observed = [0.0] * len(bins)
    for pmf, outcome in zip(rows, actual):
        p = _normalise(pmf)
        for goals, prob in enumerate(p):
            pred_mass[_bin_index(goals, bins)] += prob
        observed[_bin_index(int(outcome), bins)] += 1.0
    weights = []
    for pred, obs in zip(pred_mass, observed):
        denominator = pred + smoothing
        weights.append((obs + smoothing) / denominator if denominator else 1.0)
    return BinCalibrator(tuple(bins), tuple(weights), len(rows), smoothing)


def total_goals_calibrator(predicted_pmfs, actual_total_goals, smoothing=20.0):
    return fit_bin_calibrator(
        predicted_pmfs, actual_total_goals, TOTAL_GOAL_BINS, smoothing
    )


def net_goal_calibrator(predicted_pmfs, actual_absolute_goal_difference, smoothing=20.0):
    """Fit a separate calibration for absolute goal-difference probabilities."""
    return fit_bin_calibrator(
        predicted_pmfs, actual_absolute_goal_difference, NET_GOAL_BINS, smoothing
    )


def asian_total_settlement(total_goals: int, line: float, side: str = "over") -> str:
    """Settle over/under for a standard Asian total line, including quarter lines."""
    if total_goals < 0:
        raise ValueError("total_goals must be non-negative")
    if side not in ("over", "under"):
        raise ValueError("side must be 'over' or 'under'")
    quarter = round(line * 4) / 4
    if abs(quarter - line) > 1e-9:
        raise ValueError("line must be a standard quarter-goal Asian total")
    if abs((line * 2) - round(line * 2)) < 1e-9:
        delta = total_goals - line
        over_result = "win" if delta > 0 else "loss" if delta < 0 else "push"
    else:
        lower, upper = line - 0.25, line + 0.25
        a = asian_total_settlement(total_goals, lower, "over")
        b = asian_total_settlement(total_goals, upper, "over")
        if a == b:
            over_result = a
        elif {a, b} == {"win", "push"}:
            over_result = "half_win"
        elif {a, b} == {"loss", "push"}:
            over_result = "half_loss"
        else:
            over_result = "push"
    if side == "over":
        return over_result
    return {"win": "loss", "loss": "win", "push": "push",
            "half_win": "half_loss", "half_loss": "half_win"}[over_result]


def multiclass_brier(probabilities: Sequence[Sequence[float]], outcomes: Sequence[int]) -> float:
    if len(probabilities) != len(outcomes) or not outcomes:
        raise ValueError("probabilities and outcomes must be non-empty and aligned")
    total = 0.0
    for probs, outcome in zip(probabilities, outcomes):
        p = _normalise(probs)
        if outcome < 0 or outcome >= len(p):
            raise ValueError("outcome index outside probability vector")
        total += sum((prob - (1.0 if i == outcome else 0.0)) ** 2 for i, prob in enumerate(p))
    return total / len(outcomes)
