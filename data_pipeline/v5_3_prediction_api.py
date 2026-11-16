"""Callable V5.3 prediction API with append-only trace persistence.

This is an integration adapter, not a web server or an automatic daily scheduler.
Call predict_and_archive() from a trusted fixture/odds collector or prediction job.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

from model_validation.v5_3_prediction_engine import predict_match
from data_pipeline.prediction_snapshot_writer import init_db, append_prediction, append_market

BEIJING = ZoneInfo("Asia/Shanghai")


def _parse_aware(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _iso_beijing(value: datetime) -> str:
    return value.astimezone(BEIJING).isoformat(timespec="seconds")


def _canonical_sha256(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def predict_and_archive(
    *,
    db_path: str,
    match_id: str,
    prediction_id: str,
    git_commit_sha: str,
    home_team: str,
    away_team: str,
    kickoff_iso: str,
    snapshot_time_iso: str,
    odds_provider: str,
    odds_source_url: str,
    odds_verified: bool,
    goal_line: float,
    over_odds: float,
    under_odds: float,
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    provider_event_timestamp_iso: Optional[str] = None,
    sporttery_handicap_line: Optional[float] = None,
    feature_snapshot_id: Optional[str] = None,
    opening_goal_line: Optional[float] = None,
    opening_over_odds: Optional[float] = None,
    opening_under_odds: Optional[float] = None,
    over_price_move_pct: Optional[float] = None,
    under_price_move_pct: Optional[float] = None,
    max_goals: int = 15,
) -> Dict[str, Any]:
    """Generate one V5.3 prediction and append immutable prediction/market traces.

    match_id must be the repository's already-verified internal fixture ID.
    Odds must be verified pre-kickoff decimal prices. This function does not
    fetch odds or invent provider timestamps.
    """
    required_text = {
        "db_path": db_path, "match_id": match_id, "prediction_id": prediction_id,
        "git_commit_sha": git_commit_sha, "odds_provider": odds_provider,
        "odds_source_url": odds_source_url,
    }
    for key, value in required_text.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")

    kickoff = _parse_aware(kickoff_iso, "kickoff_iso")
    snapshot = _parse_aware(snapshot_time_iso, "snapshot_time_iso")
    if snapshot >= kickoff:
        raise ValueError("prediction snapshot must be strictly before kickoff")
    provider_event = None
    if provider_event_timestamp_iso:
        provider_event = _parse_aware(
            provider_event_timestamp_iso, "provider_event_timestamp_iso"
        )
        if provider_event >= kickoff:
            raise ValueError("provider event timestamp is not pre-kickoff")

    model_inputs = {
        "engine": "V5.3-direct-matrix-repair-2026-11-16",
        "goal_line": goal_line, "over_odds": over_odds, "under_odds": under_odds,
        "home_odds": home_odds, "draw_odds": draw_odds, "away_odds": away_odds,
        "max_goals": max_goals,
    }
    config_sha = _canonical_sha256(model_inputs)

    result = predict_match(
        home_team=home_team, away_team=away_team,
        kickoff_iso=kickoff.isoformat(), snapshot_time_iso=snapshot.isoformat(),
        odds_source=f"{odds_provider}:{odds_source_url}",
        odds_verified=odds_verified,
        goal_line=goal_line, over_odds=over_odds, under_odds=under_odds,
        home_odds=home_odds, draw_odds=draw_odds, away_odds=away_odds,
        opening_goal_line=opening_goal_line,
        opening_over_odds=opening_over_odds,
        opening_under_odds=opening_under_odds,
        over_price_move_pct=over_price_move_pct,
        under_price_move_pct=under_price_move_pct,
        max_goals=max_goals,
    )

    first = result["top_scores"][0]
    second = result["top_scores"][1]
    record = {
        "prediction_id": prediction_id,
        "match_id": match_id,
        "prediction_timestamp_utc": _iso_utc(snapshot),
        "prediction_timestamp_beijing": _iso_beijing(snapshot),
        "kickoff_utc": _iso_utc(kickoff),
        "model_name": "V5.3-direct-matrix-repair",
        "model_version": result["model_version"],
        "git_commit_sha": git_commit_sha,
        "config_sha256": config_sha,
        "feature_snapshot_id": feature_snapshot_id,
        "home_win_prob": result["p_home"],
        "draw_prob": result["p_draw"],
        "away_win_prob": result["p_away"],
        "lambda_home": result["lambda_home"],
        "lambda_away": result["lambda_away"],
        "score1_home": first["home"], "score1_away": first["away"],
        "score2_home": second["home"], "score2_away": second["away"],
        "sporttery_handicap_line": sporttery_handicap_line,
        "engine_version": result["engine_version"],
        "input_market": result["input_market"],
        "draw_exact_probabilities": result["draw_exact_probabilities"],
        "goal_bins": result["goal_bins"],
        "absolute_net_goal_bins": result["absolute_net_goal_bins"],
        "away_goal_bins": result["away_goal_bins"],
        "score_probability_matrix": result["score_probability_matrix"],
        "top_scores": result["top_scores"],
        "validation_status": result["validation_status"],
    }

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    con = init_db(str(db_file))
    try:
        append_prediction(con, record)
        retrieved = datetime.now(timezone.utc)
        raw_market = {
            "provider": odds_provider,
            "source_url": odds_source_url,
            "provider_event_timestamp": provider_event.isoformat() if provider_event else None,
            "retrieved_at_utc": _iso_utc(retrieved),
            "odds_verified_pre_kickoff": odds_verified,
            "markets": {
                "1x2": {"home": home_odds, "draw": draw_odds, "away": away_odds},
                "totals": {"line": goal_line, "over": over_odds, "under": under_odds},
            },
        }
        append_market(con, {
            "market_snapshot_id": str(uuid.uuid4()),
            "match_id": match_id,
            "prediction_id": prediction_id,
            "provider": odds_provider,
            "market_type": "1X2+TOTALS",
            "source_url": odds_source_url,
            "provider_event_timestamp_utc": _iso_utc(provider_event) if provider_event else None,
            "retrieved_at_utc": _iso_utc(retrieved),
            "retrieved_at_beijing": _iso_beijing(retrieved),
            "timestamp_quality": "PROVIDER_EVENT_AND_RETRIEVAL_TIME" if provider_event else "RETRIEVAL_TIME_ONLY",
            "normalized": raw_market["markets"],
            "raw_payload": raw_market,
        })
    finally:
        con.close()

    result["archive"] = {
        "db_path": str(db_file),
        "prediction_id": prediction_id,
        "match_id": match_id,
        "config_sha256": config_sha,
        "prediction_and_market_trace_appended": True,
    }
    return result
