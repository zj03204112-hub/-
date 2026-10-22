"""Leakage-safe total-goal tail calibration for shadow V5.2 experiments.

This module does not alter V5.1 production behavior. Fit only on chronological
training/calibration data; never fit on the evaluation holdout.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Sequence

BINS = ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, None))


def _bin_index(total: int) -> int:
    if total < 0:
        raise ValueError("goals total cannot be negative")
    return min(total, 6)


def _normalise(values: Sequence[float]) -> list[float]:
    if not values or any((not isfinite(x) or x < 0) for x in values):
        raise ValueError("probabilities must be finite, non-negative, and non-empty")
    s = sum(values)
    if s <= 0:
        raise ValueError("probabilities must have positive sum")
    return [x / s for x in values]


@dataclass(frozen=True)
class TailCalibration:
    """Observed-vs-predicted bin-ratio calibrator fitted on training data only."""

    multipliers: tuple[float, ...]
    train_matches: int
    smoothing: float

    @classmethod
    def fit(
        cls,
        predicted_distributions: Iterable[Sequence[float]],
        observed_totals: Iterable[int],
        smoothing: float = 20.0,
        clip: tuple[float, float] = (0.5, 2.0),
    ) -> "TailCalibration":
        pred_rows = list(predicted_distributions)
        actuals = list(observed_totals)
        if len(pred_rows) != len(actuals) or not actuals:
            raise ValueError("training distributions and outcomes must have equal non-zero length")
        if smoothing <= 0 or clip[0] <= 0 or clip[1] < clip[0]:
            raise ValueError("invalid smoothing or clip bounds")

        predicted = [0.0] * 7
        observed = [0.0] * 7
        for dist, actual in zip(pred_rows, actuals):
            if not isinstance(actual, int) or actual < 0:
                raise ValueError("observed totals must be non-negative integers")
            p = _normalise(dist)
            # Any probability beyond the supplied score range is assumed to be
            # represented by the final supplied cell; callers should include 6+.
            for goals, prob in enumerate(p):
                predicted[_bin_index(goals)] += prob
            observed[_bin_index(actual)] += 1.0

        n = len(actuals)
        multipliers = []
        for i in range(7):
            # Add symmetric pseudo-counts to avoid unstable ratios in rare tails.
            target_rate = (observed[i] + smoothing / 7.0) / (n + smoothing)
            predicted_rate = (predicted[i] + smoothing / 7.0) / (n + smoothing)
            ratio = target_rate / predicted_rate if predicted_rate else 1.0
            multipliers.append(min(clip[1], max(clip[0], ratio)))
        return cls(tuple(multipliers), n, smoothing)

    def apply(self, probabilities: Sequence[float]) -> list[float]:
        """Reweight total-goal probabilities by bins, then renormalize."""
        p = _normalise(probabilities)
        weighted = [value * self.multipliers[_bin_index(goals)]
                    for goals, value in enumerate(p)]
        return _normalise(weighted)


def total_goal_bins(probabilities: Sequence[float]) -> dict[str, float]:
    """Return probability mass for totals 0,1,2,3,4,5,6+."""
    p = _normalise(probabilities)
    result = {str(i): 0.0 for i in range(6)}
    result["6+"] = 0.0
    for goals, value in enumerate(p):
        result["6+" if goals >= 6 else str(goals)] += value
    return result
