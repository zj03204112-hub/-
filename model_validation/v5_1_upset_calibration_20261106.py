#!/usr/bin/env python3
"""Experimental V5.1 upset-aware calibration. Never rewrites original predictions.

Input: JSON {"scores": [{"home": int, "away": int, "probability": float}, ...],
             "features": {"form_gap": float, "lineup_gap": float,
                          "schedule_gap": float, "motivation_gap": float}}
Feature gaps are away minus home, each in [-1, 1]; omit unknown features.
Output: recalibrated scoreline distribution and derived 1X2/handicap probabilities.
All coefficients are experimental and NOT validated. Never use final scores as features.
"""
from __future__ import annotations
import math
from typing import Mapping

VERSION = "V5.1-upset-shadow-20261106"
FEATURE_WEIGHTS = {"form_gap": 0.20, "lineup_gap": 0.15,
                   "schedule_gap": 0.10, "motivation_gap": 0.10}
MAX_SHIFT = 0.35
FAVORITE_CAP = 0.65  # Experimental shrinkage trigger, not hard probability cap.
SHRINK = 0.15


def recalibrate(scores: list[dict], features: Mapping[str, float] | None = None,
                handicap: int = -1) -> dict:
    if not scores:
        raise ValueError("scores required")
    features = features or {}
    shift = 0.0
    for key, weight in FEATURE_WEIGHTS.items():
        if key in features and features[key] is not None:
            value = float(features[key])
            if not math.isfinite(value) or not -1 <= value <= 1:
                raise ValueError(f"{key} must be in [-1,1]")
            shift += weight * value
    shift = max(-MAX_SHIFT, min(MAX_SHIFT, shift))
    seen = set()
    base = []
    for row in scores:
        h, a, p = int(row["home"]), int(row["away"]), float(row["probability"])
        if h < 0 or a < 0 or not math.isfinite(p) or p < 0:
            raise ValueError("invalid scoreline")
        if (h, a) in seen:
            raise ValueError("duplicate scoreline")
        seen.add((h, a))
        base.append((h, a, p))
    total = sum(p for _, _, p in base)
    if total <= 0:
        raise ValueError("zero probability mass")
    base = [(h, a, p / total) for h, a, p in base]
    home_p = sum(p for h, a, p in base if h > a)
    away_p = sum(p for h, a, p in base if h < a)
    # Shrink overconfident favorite scoreline mass towards a tempered distribution.
    # This retains a coherent scoreline distribution; no synthetic scorelines are added.
    favorite = 1 if home_p > away_p else -1
    trigger = max(home_p, away_p) > FAVORITE_CAP
    adjusted = []
    for h, a, p in base:
        weight = math.exp(max(-3, min(3, shift * (a - h))))
        if trigger:
            if (h - a) * favorite > 0:
                weight *= 1 - SHRINK
            elif (h - a) * favorite < 0:
                weight *= 1 + SHRINK
        adjusted.append((h, a, p * weight))
    z = sum(p for _, _, p in adjusted)
    adjusted = [(h, a, p / z) for h, a, p in adjusted]
    outcomes = {
        "home": sum(p for h, a, p in adjusted if h > a),
        "draw": sum(p for h, a, p in adjusted if h == a),
        "away": sum(p for h, a, p in adjusted if h < a),
    }
    handicap_probs = {
        "handicap_home": sum(p for h, a, p in adjusted if h - a + handicap > 0),
        "handicap_draw": sum(p for h, a, p in adjusted if h - a + handicap == 0),
        "handicap_away": sum(p for h, a, p in adjusted if h - a + handicap < 0),
    }
    return {"version": VERSION, "status": "unvalidated_experiment",
            "scores": [{"home": h, "away": a, "probability": p}
                       for h, a, p in adjusted],
            "outcomes": outcomes, "handicap": handicap_probs}
