"""V5.2 candidate prediction engine entry point.

Uses verified pre-match decimal odds (1X2 + half-goal O/U) to construct
a joint score distribution, then applies the isolated total-goal tail
reweighting layer. This is a deployable candidate, not evidence of improved
accuracy. Inputs must be timestamped and verified by the caller.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from model_validation.v5_2_market_lambda import predict_from_markets
from model_validation.v5_2_goal_tail_shadow import reweight_score_grid


ENGINE_VERSION = "V5.2-candidate-2026-10-28"


def predict_match(
    *,
    home_team: str,
    away_team: str,
    kickoff_iso: str,
    snapshot_time_iso: str,
    odds_source: str,
    odds_verified: bool,
    goal_line: float,
    over_odds: float,
    under_odds: float,
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    max_goals: int = 15,
) -> Dict[str, Any]:
    """Generate a probability distribution from verified pre-match market data.

    Does not scrape fixtures, verify source authenticity, calculate Sporttery
    handicap outcomes, or persist snapshots; those are external pipeline duties.
    """
    if not home_team.strip() or not away_team.strip() or home_team == away_team:
        raise ValueError("distinct non-empty home_team and away_team are required")
    if not odds_verified:
        raise ValueError("refusing prediction: pre-match odds are not verified")
    if not odds_source.strip():
        raise ValueError("odds_source is required")
    try:
        snapshot = datetime.fromisoformat(snapshot_time_iso.replace("Z", "+00:00"))
        kickoff = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("kickoff_iso and snapshot_time_iso must be ISO-8601") from exc
    if snapshot.tzinfo is None or kickoff.tzinfo is None:
        raise ValueError("timestamps must include timezone information")
    if snapshot >= kickoff:
        raise ValueError("odds snapshot must be strictly before kickoff")

    base = predict_from_markets(
        over_odds=over_odds,
        under_odds=under_odds,
        goal_line=goal_line,
        home_odds=home_odds,
        draw_odds=draw_odds,
        away_odds=away_odds,
        max_goals=max_goals,
    )
    dist = base["distribution"]
    # Reconstruct the complete independent-Poisson score grid from the inferred
    # lambdas so that the shadow reweighting is applied to every scoreline.
    from model_validation.v5_2_market_lambda import poisson_pmf
    hp = poisson_pmf(float(base["lambda_home"]), max_goals)
    ap = poisson_pmf(float(base["lambda_away"]), max_goals)
    rows = [(h, a, ph * pa) for h, ph in enumerate(hp) for a, pa in enumerate(ap)]
    adjusted = reweight_score_grid(rows)

    return {
        "engine_version": ENGINE_VERSION,
        "model_version": adjusted["version"],
        "validation_status": "CANDIDATE_NOT_BACKTESTED",
        "home_team": home_team,
        "away_team": away_team,
        "kickoff": kickoff.isoformat(),
        "snapshot_time": snapshot.isoformat(),
        "odds_source": odds_source,
        "input_market": base["market"],
        "lambda_home": base["lambda_home"],
        "lambda_away": base["lambda_away"],
        "lambda_total": base["lambda_total"],
        "top_scores": adjusted["top_scores"],
        "p_home": adjusted["p_home"],
        "p_draw": adjusted["p_draw"],
        "p_away": adjusted["p_away"],
        "goal_bins": adjusted["goal_bins"],
        "probability_mass": adjusted["probability_mass"],
        "production_v5_1_changed": False,
        "warnings": [
            "Tail multipliers are experimental and have not been calibrated on a frozen holdout.",
            "No automated fixture ingestion, Sporttery handicap mapping, or persistence is performed by this entry point.",
            "Use a separate append-only prediction snapshot writer for archival."
        ],
    }
