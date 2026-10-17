"""Experimental V5.1 European 1X2 odds -> goal-intensity correction.

This is an isolated shadow-test module. It does not modify the frozen V5.1
production pipeline. European 1X2 odds are de-vigged, then fitted to an
independent-Poisson score distribution. The fitted market lambdas are blended
with model lambdas at a conservative, configurable weight (default 0.15).
Weights must be selected with chronological train/validation, never on the
frozen holdout. This module does not use Asian handicap odds.
"""
from __future__ import annotations

from math import exp, factorial
from typing import Dict, Tuple


def no_vig_1x2(home_odds: float, draw_odds: float, away_odds: float) -> Tuple[float, float, float]:
    odds = (home_odds, draw_odds, away_odds)
    if any(o <= 1.0 for o in odds):
        raise ValueError("Decimal 1X2 odds must all be > 1.0")
    raw = tuple(1.0 / o for o in odds)
    total = sum(raw)
    if total <= 0:
        raise ValueError("Invalid odds")
    return tuple(p / total for p in raw)


def _poisson_probs(lam: float, max_goals: int = 12) -> Tuple[float, ...]:
    if lam <= 0:
        raise ValueError("Expected goals must be > 0")
    vals = [exp(-lam) * (lam ** k) / factorial(k) for k in range(max_goals + 1)]
    # Normalize the truncated tail so outcome probabilities sum to one.
    total = sum(vals)
    return tuple(v / total for v in vals)


def _outcome_probs(home_lam: float, away_lam: float, max_goals: int = 12) -> Tuple[float, float, float]:
    hp = _poisson_probs(home_lam, max_goals)
    ap = _poisson_probs(away_lam, max_goals)
    home = draw = away = 0.0
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            p = ph * pa
            if h > a:
                home += p
            elif h == a:
                draw += p
            else:
                away += p
    s = home + draw + away
    return home / s, draw / s, away / s


def fit_market_lambdas(
    target_1x2: Tuple[float, float, float],
    min_lambda: float = 0.20,
    max_lambda: float = 4.00,
    step: float = 0.05,
) -> Tuple[float, float]:
    """Fit independent-Poisson lambdas to no-vig 1X2 probabilities by grid search."""
    if abs(sum(target_1x2) - 1.0) > 1e-6:
        raise ValueError("Target 1X2 probabilities must sum to 1")
    if step <= 0 or max_lambda < min_lambda:
        raise ValueError("Invalid grid settings")
    grid = []
    n = int(round((max_lambda - min_lambda) / step))
    for i in range(n + 1):
        lam = min_lambda + i * step
        grid.append((lam, _poisson_probs(lam)))
    # Precompute outcome probabilities for each candidate pair.
    best_score = float("inf")
    best_pair = (1.35, 1.10)
    for home_lam, hp in grid:
        for away_lam, ap in grid:
            ph = pd = pa = 0.0
            for h, p_h in enumerate(hp):
                for a, p_a in enumerate(ap):
                    p = p_h * p_a
                    if h > a:
                        ph += p
                    elif h == a:
                        pd += p
                    else:
                        pa += p
            total = ph + pd + pa
            pred = (ph / total, pd / total, pa / total)
            score = sum((pred[i] - target_1x2[i]) ** 2 for i in range(3))
            if score < best_score:
                best_score, best_pair = score, (home_lam, away_lam)
    return best_pair


def correct_goal_lambdas(
    model_home_lambda: float,
    model_away_lambda: float,
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    market_weight: float = 0.15,
) -> Dict[str, object]:
    """Return baseline and experimental corrected lambdas; no production mutation."""
    if model_home_lambda <= 0 or model_away_lambda <= 0:
        raise ValueError("Model lambdas must be > 0")
    if not 0.0 <= market_weight <= 0.30:
        raise ValueError("Shadow-test market_weight must be between 0 and 0.30")
    market_probs = no_vig_1x2(home_odds, draw_odds, away_odds)
    market_home, market_away = fit_market_lambdas(market_probs)
    corrected_home = (1.0 - market_weight) * model_home_lambda + market_weight * market_home
    corrected_away = (1.0 - market_weight) * model_away_lambda + market_weight * market_away
    return {
        "mode": "shadow_test_only",
        "market_weight": market_weight,
        "market_1x2_no_vig": {
            "home": market_probs[0], "draw": market_probs[1], "away": market_probs[2]
        },
        "model_lambda": {
            "home": model_home_lambda, "away": model_away_lambda,
            "total": model_home_lambda + model_away_lambda
        },
        "market_fitted_lambda": {
            "home": market_home, "away": market_away, "total": market_home + market_away
        },
        "corrected_lambda": {
            "home": corrected_home, "away": corrected_away,
            "total": corrected_home + corrected_away
        },
        "warning": "Experimental only. Validate chronologically against the same frozen sample before any production adoption."
    }
