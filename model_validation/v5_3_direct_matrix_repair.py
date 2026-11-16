"""V5.3 direct score-matrix repair layer.

Rule-based, explicitly experimental rebalancing of one complete joint score
matrix. It does not train or fit parameters. V5.1 and the existing V5.2 files
remain untouched. Every reported score/H-D-A/goal/net-goal probability is
derived from this same normalized matrix.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from model_validation.v5_2_market_lambda import poisson_pmf

DEFAULT_WEIGHTS = {
    "draw_exact": {"0-0": 1.08, "1-1": 1.10, "2-2": 1.08},
    "total_goals": {"5": 1.08, "6": 1.10, "7": 1.10, "8+": 1.08},
    "away_goals": {"2": 1.04, "3+": 1.08},
    "absolute_margin": {"0": 1.03, "1": 1.00, "2": 1.00, "3": 1.02, "4": 1.03, "5+": 1.02},
}


def _support(lam: float, requested_min: int) -> int:
    if not math.isfinite(lam) or lam < 0 or requested_min < 0:
        raise ValueError("lambdas and requested_min must be finite and non-negative")
    # Keep the omitted Poisson tail negligible even for high-scoring fixtures.
    return max(requested_min, int(math.ceil(lam + 12.0 * math.sqrt(lam + 1.0) + 12.0)))


def _bucket_total(total: int) -> str:
    return str(total) if total <= 7 else "8+"


def _bucket_margin(margin: int) -> str:
    return str(margin) if margin <= 4 else "5+"


def _validate_weights(weights: Mapping[str, Any]) -> None:
    for group, required in DEFAULT_WEIGHTS.items():
        if group not in weights:
            raise ValueError(f"missing weight group: {group}")
        for key in required:
            value = float(weights[group][key])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"weight {group}.{key} must be finite and positive")


def build_score_matrix(
    *,
    lambda_home: float,
    lambda_away: float,
    market_home_probability: float,
    market_draw_probability: float,
    market_away_probability: float,
    min_goals: int = 15,
    weights: Mapping[str, Any] = DEFAULT_WEIGHTS,
) -> Dict[str, Any]:
    """Build and rebalance a complete score matrix from market-derived lambdas."""
    if not all(math.isfinite(x) and x >= 0 for x in (lambda_home, lambda_away)):
        raise ValueError("lambdas must be finite and non-negative")
    market = (market_home_probability, market_draw_probability, market_away_probability)
    if (not all(math.isfinite(x) and 0 <= x <= 1 for x in market)
            or abs(sum(market) - 1.0) > 1e-6):
        raise ValueError("market H/D/A probabilities must be in [0,1] and sum to 1")
    _validate_weights(weights)

    max_goals = max(_support(lambda_home, min_goals), _support(lambda_away, min_goals))
    hp = poisson_pmf(lambda_home, max_goals)
    ap = poisson_pmf(lambda_away, max_goals)
    sh, sa = sum(hp), sum(ap)
    hp = [p / sh for p in hp]
    ap = [p / sa for p in ap]

    rows: List[Tuple[int, int, float, float]] = []
    home_is_market_favourite = market_home_probability > market_away_probability
    for h, ph in enumerate(hp):
        for a, pa in enumerate(ap):
            raw = ph * pa
            weight = 1.0
            exact = f"{h}-{a}"
            if h == a and exact in weights["draw_exact"]:
                weight *= float(weights["draw_exact"][exact])
            total_key = _bucket_total(h + a)
            weight *= float(weights["total_goals"].get(total_key, 1.0))
            if home_is_market_favourite and a >= 2:
                weight *= float(weights["away_goals"]["3+"] if a >= 3
                                else weights["away_goals"]["2"])
            margin_key = _bucket_margin(abs(h - a))
            weight *= float(weights["absolute_margin"][margin_key])
            rows.append((h, a, raw, raw * weight))

    mass = sum(row[3] for row in rows)
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError("score matrix has no positive finite probability mass")

    width = max_goals + 1
    matrix = [[0.0 for _ in range(width)] for _ in range(width)]
    total_bins = {str(k): 0.0 for k in range(8)}
    total_bins["8+"] = 0.0
    margin_bins = {str(k): 0.0 for k in range(5)}
    margin_bins["5+"] = 0.0
    away_bins = {str(k): 0.0 for k in range(4)}
    away_bins["4+"] = 0.0
    exact_draws = {"0-0": 0.0, "1-1": 0.0, "2-2": 0.0}
    p_home = p_draw = p_away = 0.0
    scorelines = []

    for h, a, _, weighted in rows:
        p = weighted / mass
        matrix[h][a] = p
        total_bins[_bucket_total(h + a)] += p
        margin_bins[_bucket_margin(abs(h - a))] += p
        away_bins[str(a) if a <= 3 else "4+"] += p
        if h == a:
            p_draw += p
            if f"{h}-{a}" in exact_draws:
                exact_draws[f"{h}-{a}"] = p
        elif h > a:
            p_home += p
        else:
            p_away += p
        scorelines.append({"home": h, "away": a, "score": f"{h}-{a}", "probability": p})

    scorelines.sort(key=lambda row: row["probability"], reverse=True)
    total_mass = sum(sum(row) for row in matrix)
    hda_mass = p_home + p_draw + p_away
    if abs(total_mass - 1.0) > 1e-10 or abs(hda_mass - 1.0) > 1e-10:
        raise ArithmeticError("matrix probability mass failed normalization")
    total_pmf = [0.0 for _ in range(2 * max_goals + 1)]
    for h, a, _, weighted in rows:
        total_pmf[h + a] += weighted / mass

    return {
        "module_version": "V5.3-direct-matrix-repair-v1",
        "validation_status": "DIRECT_RULE_CHANGE_NOT_TRAINED_OR_BACKTESTED",
        "max_goals_per_team": max_goals,
        "score_probability_matrix": matrix,
        "scorelines": scorelines,
        "top_scores": scorelines[:2],
        "p_home": p_home,
        "p_draw": p_draw,
        "p_away": p_away,
        "market_hda_devig": {"home": market[0], "draw": market[1], "away": market[2]},
        "matrix_market_hda_gap": {
            "home": p_home - market[0], "draw": p_draw - market[1], "away": p_away - market[2]
        },
        "draw_exact_probabilities": exact_draws,
        "total_goal_bins": total_bins,
        "absolute_net_goal_bins": margin_bins,
        "away_goal_bins": away_bins,
        "total_goal_pmf": total_pmf,
        "probability_mass": total_mass,
        "weights": {k: dict(v) for k, v in weights.items()},
        "production_v5_1_changed": False,
    }
