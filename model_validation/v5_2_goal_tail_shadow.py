"""Experimental total-goal tail sensitivity adjustment, isolated from V5.1.

This is an explicit shadow layer for testing, not a claim of improved accuracy.
It reweights scoreline probabilities by total-goal bucket, then renormalizes.
The base market-derived lambdas are not changed, avoiding double-counting an
arbitrary global lambda uplift. Keep separate until evaluated on chronological
holdout data.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Mapping, Tuple

# Conservative, explicit experiment: slightly reduce low totals and increase
# high totals. Values are likelihood multipliers, not probability percentages.
DEFAULT_TOTAL_GOAL_MULTIPLIERS = {
    "0-1": 0.96,
    "2": 0.98,
    "3-4": 1.04,
    "5-6": 1.12,
    "7+": 1.18,
}


def total_goal_bucket(total: int) -> str:
    if total < 0:
        raise ValueError("total goals cannot be negative")
    if total <= 1:
        return "0-1"
    if total == 2:
        return "2"
    if total <= 4:
        return "3-4"
    if total <= 6:
        return "5-6"
    return "7+"


def reweight_score_grid(
    score_probabilities: Iterable[Tuple[int, int, float]],
    multipliers: Mapping[str, float] = DEFAULT_TOTAL_GOAL_MULTIPLIERS,
) -> Dict[str, object]:
    """Reweight (home_goals, away_goals, probability) rows and normalize.

    Returns normalized scorelines, H/D/A probabilities, top two scorelines,
    and total-goal bucket probabilities. Input probabilities must be finite,
    non-negative, and have positive total mass. Multipliers must be finite
    and strictly positive for every bucket.
    """
    required = set(DEFAULT_TOTAL_GOAL_MULTIPLIERS)
    if not required.issubset(multipliers):
        raise ValueError("multipliers must define all five total-goal buckets")
    if any(not math.isfinite(float(multipliers[k])) or float(multipliers[k]) <= 0
           for k in required):
        raise ValueError("all multipliers must be finite and positive")

    rows: List[Tuple[int, int, float, str]] = []
    for h, a, p in score_probabilities:
        if not isinstance(h, int) or not isinstance(a, int) or h < 0 or a < 0:
            raise ValueError("goals must be non-negative integers")
        if not math.isfinite(p) or p < 0:
            raise ValueError("score probabilities must be finite and non-negative")
        bucket = total_goal_bucket(h + a)
        rows.append((h, a, p * float(multipliers[bucket]), bucket))

    mass = sum(row[2] for row in rows)
    if not rows or not math.isfinite(mass) or mass <= 0:
        raise ValueError("score grid must have positive probability mass")

    normalized = [(h, a, p / mass, bucket) for h, a, p, bucket in rows]
    ph = pd = pa = 0.0
    bins = {k: 0.0 for k in ("0-1", "2", "3-4", "5-6", "7+")}
    for h, a, p, bucket in normalized:
        bins[bucket] += p
        if h > a:
            ph += p
        elif h == a:
            pd += p
        else:
            pa += p

    ranked = sorted(normalized, key=lambda x: x[2], reverse=True)
    return {
        "version": "V5.2-shadow-goal-tail-v1",
        "scorelines": [
            {"home": h, "away": a, "probability": p}
            for h, a, p, _ in normalized
        ],
        "top_scores": [
            {"home": h, "away": a, "probability": p}
            for h, a, p, _ in ranked[:2]
        ],
        "p_home": ph,
        "p_draw": pd,
        "p_away": pa,
        "goal_bins": bins,
        "multipliers": {k: float(multipliers[k]) for k in
                        ("0-1", "2", "3-4", "5-6", "7+")},
        "probability_mass": sum(p for _, _, p, _ in normalized),
        "production_v5_1_changed": False,
        "validation_status": "EXPERIMENTAL_NOT_BACKTESTED",
    }
