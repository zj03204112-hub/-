"""Shadow-only goal expectation uplift experiment; never imported by production V5.1.

Default hypothesis:
- increase total expected goals by 8%;
- if the ORIGINAL total lambda is >= 2.8, apply a further 10% multiplier;
- preserve the home/away lambda ratio.
These are experimental parameters, not validated improvements.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

BASE_UPLIFT = 0.08
HIGH_TOTAL_THRESHOLD = 2.8
HIGH_TOTAL_EXTRA_UPLIFT = 0.10


def uplift_lambdas(
    lambda_home: float,
    lambda_away: float,
    *,
    base_uplift: float = BASE_UPLIFT,
    high_total_threshold: float = HIGH_TOTAL_THRESHOLD,
    high_total_extra_uplift: float = HIGH_TOTAL_EXTRA_UPLIFT,
) -> Tuple[float, float]:
    """Return shadow-adjusted (home, away) lambdas without changing their ratio."""
    values = (lambda_home, lambda_away, base_uplift, high_total_threshold, high_total_extra_uplift)
    if not all(math.isfinite(x) for x in values):
        raise ValueError("All parameters must be finite numbers")
    if lambda_home < 0 or lambda_away < 0 or lambda_home + lambda_away <= 0:
        raise ValueError("Expected-goal lambdas must be non-negative with a positive total")
    if base_uplift < 0 or high_total_extra_uplift < 0 or high_total_threshold < 0:
        raise ValueError("Uplift rates and threshold must be non-negative")

    original_total = lambda_home + lambda_away
    factor = 1.0 + base_uplift
    if original_total >= high_total_threshold:
        factor *= 1.0 + high_total_extra_uplift
    return lambda_home * factor, lambda_away * factor


def poisson_pmf(lam: float, max_goals: int = 12) -> List[float]:
    """Poisson probabilities for 0..max_goals; remaining tail is omitted."""
    if not math.isfinite(lam) or lam < 0:
        raise ValueError("lambda must be finite and non-negative")
    if max_goals < 0:
        raise ValueError("max_goals must be non-negative")
    p = math.exp(-lam)
    probs = [p]
    for k in range(1, max_goals + 1):
        p = p * lam / k
        probs.append(p)
    return probs


def score_distribution(
    lambda_home: float, lambda_away: float, max_goals: int = 12
) -> Dict[str, object]:
    """Calculate independent-Poisson score grid, top two scores and 1X2 probabilities."""
    home = poisson_pmf(lambda_home, max_goals)
    away = poisson_pmf(lambda_away, max_goals)
    grid = []
    p_home = p_draw = p_away = 0.0
    total_pmf = [0.0] * (2 * max_goals + 1)
    for h, ph in enumerate(home):
        for a, pa in enumerate(away):
            p = ph * pa
            grid.append((p, h, a))
            total_pmf[h + a] += p
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
    grid.sort(reverse=True)
    mass = sum(home) * sum(away)
    return {
        "top_scores": [(h, a, p) for p, h, a in grid[:2]],
        "home_win_probability": p_home,
        "draw_probability": p_draw,
        "away_win_probability": p_away,
        "total_goals_pmf": total_pmf,
        "grid_probability_mass": mass,
        "tail_probability_omitted": max(0.0, 1.0 - mass),
    }


def shadow_prediction(lambda_home: float, lambda_away: float, max_goals: int = 12) -> Dict[str, object]:
    """Apply uplift and return shadow values; caller must retain the original prediction."""
    adjusted_home, adjusted_away = uplift_lambdas(lambda_home, lambda_away)
    return {
        "original_lambda_home": lambda_home,
        "original_lambda_away": lambda_away,
        "original_lambda_total": lambda_home + lambda_away,
        "adjusted_lambda_home": adjusted_home,
        "adjusted_lambda_away": adjusted_away,
        "adjusted_lambda_total": adjusted_home + adjusted_away,
        "uplift_factor": (adjusted_home + adjusted_away) / (lambda_home + lambda_away),
        "distribution": score_distribution(adjusted_home, adjusted_away, max_goals),
        "mode": "shadow_only",
        "production_v5_1_changed": False,
    }
