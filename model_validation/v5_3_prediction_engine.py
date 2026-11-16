"""V5.3 candidate prediction engine using a single repaired score matrix.

This is a direct rule-based candidate, not a trained or validated production
model. It requires verified, timestamped pre-kickoff decimal odds.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from model_validation.v5_2_market_lambda import predict_from_markets
from model_validation.v5_3_direct_matrix_repair import build_score_matrix
from model_validation.v5_2_big_goal_awareness import assess_big_goal_sensitivity, high_total_score_candidates
from model_validation.v5_2_asian_totals_settlement import settle_probabilities


ENGINE_VERSION = "V5.3-direct-matrix-repair-2026-11-16"


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
    opening_goal_line: float | None = None,
    opening_over_odds: float | None = None,
    opening_under_odds: float | None = None,
    over_price_move_pct: float | None = None,
    under_price_move_pct: float | None = None,
    max_goals: int = 15,
) -> Dict[str, Any]:
    if not home_team.strip() or not away_team.strip() or home_team == away_team:
        raise ValueError("distinct non-empty home_team and away_team are required")
    if not odds_verified or not odds_source.strip():
        raise ValueError("verified pre-match odds and odds_source are required")
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
        over_odds=over_odds, under_odds=under_odds, goal_line=goal_line,
        home_odds=home_odds, draw_odds=draw_odds, away_odds=away_odds,
        max_goals=max_goals,
    )
    market = base["market"]
    matrix = build_score_matrix(
        lambda_home=float(base["lambda_home"]),
        lambda_away=float(base["lambda_away"]),
        market_home_probability=float(market["p_home_devig"]),
        market_draw_probability=float(market["p_draw_devig"]),
        market_away_probability=float(market["p_away_devig"]),
        min_goals=max_goals,
    )
    big_goal = assess_big_goal_sensitivity(
        current_line=goal_line, current_over_odds=over_odds, current_under_odds=under_odds,
        lambda_home=float(base["lambda_home"]), lambda_away=float(base["lambda_away"]),
        opening_line=opening_goal_line, opening_over_odds=opening_over_odds,
        opening_under_odds=opening_under_odds, over_price_move_pct=over_price_move_pct,
        under_price_move_pct=under_price_move_pct,
    )
    quarter_line_settlement = {
        str(line): {
            side: settle_probabilities(
                line=line, side=side,
                odds=over_odds if side == "over" else under_odds,
                total_goal_probabilities={i: p for i, p in enumerate(matrix["total_goal_pmf"])},
            )
            for side in ("over", "under")
        }
        for line in (2.25, 2.75)
    }
    high_tail = high_total_score_candidates(
        ((row["home"], row["away"], row["probability"]) for row in matrix["scorelines"]),
        min_total_goals=5, limit=12,
    )

    return {
        "engine_version": ENGINE_VERSION,
        "model_version": matrix["module_version"],
        "validation_status": matrix["validation_status"],
        "home_team": home_team, "away_team": away_team,
        "kickoff": kickoff.isoformat(), "snapshot_time": snapshot.isoformat(),
        "odds_source": odds_source,
        "input_market": market,
        "lambda_home": base["lambda_home"], "lambda_away": base["lambda_away"],
        "lambda_total": base["lambda_total"],
        "top_scores": matrix["top_scores"],
        "p_home": matrix["p_home"], "p_draw": matrix["p_draw"], "p_away": matrix["p_away"],
        "draw_exact_probabilities": matrix["draw_exact_probabilities"],
        "goal_bins": matrix["total_goal_bins"],
        "absolute_net_goal_bins": matrix["absolute_net_goal_bins"],
        "away_goal_bins": matrix["away_goal_bins"],
        "matrix_market_hda_gap": matrix["matrix_market_hda_gap"],
        "score_probability_matrix": matrix["score_probability_matrix"],
        "scorelines": matrix["scorelines"],
        "big_goal_awareness": big_goal,
        "asian_quarter_line_settlement_probabilities": quarter_line_settlement,
        "high_total_score_candidates": high_tail,
        "probability_mass": matrix["probability_mass"],
        "production_v5_1_changed": False,
        "warnings": [
            "Direct rule-based reweighting; no training or holdout validation has been performed.",
            "Draw, away-goal, total-goal-tail and net-margin multipliers are explicit heuristics, not learned parameters.",
            "Market H/D/A probabilities are reported separately; matrix probabilities may differ after scoreline reweighting.",
            "V5.1 and all historical prediction snapshots remain unchanged.",
        ],
    }
