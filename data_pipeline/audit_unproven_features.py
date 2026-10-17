#!/usr/bin/env python3
"""Audit whether optional V5.2 feature tables contain usable, time-safe data.

This is an audit only: it does not change V5.1 predictions or model coefficients.
Run from the repository root with FOOTBALL_DB=/path/to/football_model_database.sqlite.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB = os.environ.get("FOOTBALL_DB", "football_model_database.sqlite")
OUT = os.environ.get("FEATURE_AUDIT_OUT", "data/feature_ingestion_audit.json")
CUTOFF_HOURS = int(os.environ.get("FEATURE_CUTOFF_HOURS", "12"))


def table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def columns(con, table):
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def scalar(con, sql, args=()):
    try:
        row = con.execute(sql, args).fetchone()
        return row[0] if row else None
    except sqlite3.Error as exc:
        return {"query_error": str(exc)}


def table_audit(con, table, required, nonzero_columns=()):
    if not table_exists(con, table):
        return {"exists": False, "status": "missing_table"}
    cols = columns(con, table)
    missing = sorted(set(required) - cols)
    if missing:
        return {"exists": True, "status": "missing_columns", "missing_columns": missing}
    total = scalar(con, f'SELECT COUNT(*) FROM "{table}"')
    result = {"exists": True, "status": "schema_ok", "rows": total}
    for col in nonzero_columns:
        if col in cols:
            result[f"{col}_nonzero_rows"] = scalar(
                con, f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" IS NOT NULL AND "{col}" != 0'
            )
    return result


def main():
    if not os.path.exists(DB):
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    finished = scalar(con, """
        SELECT COUNT(*) FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
    """) or 0
    match_count = scalar(con, "SELECT COUNT(*) FROM matches") or 0

    audits = {
        "team_features": table_audit(
            con, "team_features",
            ("match_id", "team_side", "xg_for", "xg_against"),
            ("xg_for", "xg_against")
        ),
        "injuries": table_audit(
            con, "injuries",
            ("match_id", "team_side", "player", "status", "source_status",
             "observed_at", "data_cutoff", "source_url"),
            ("availability_prob", "expected_start_prob", "impact_attack", "impact_defense")
        ),
        "player_match_stats": table_audit(
            con, "player_match_stats",
            ("match_id", "team_side", "player_id", "minutes_played", "rating",
             "xg", "xa", "data_source", "observed_at"),
            ("minutes_played", "rating", "xg", "xa", "goals", "assists")
        ),
        "player_features": table_audit(
            con, "player_features",
            ("match_id", "team_side", "player_id", "start_prob", "expected_minutes",
             "attack90", "defense90", "influence90", "sample_minutes",
             "data_cutoff", "source_status"),
            ("start_prob", "expected_minutes", "attack90", "defense90", "influence90", "sample_minutes")
        ),
        "lineup_projection": table_audit(
            con, "lineup_projection",
            ("match_id", "team_side", "attack_delta", "defense_delta",
             "data_cutoff", "source_status"),
            ("attack_delta", "defense_delta", "player_count")
        ),
        "schedule_intent": table_audit(
            con, "schedule_intent",
            ("match_id", "team_side", "next_opponent", "next_match_time",
             "rest_days", "next_match_importance", "rotation_risk", "motivation_adjustment"),
            ("rest_days", "rotation_risk", "motivation_adjustment")
        ),
    }

    # A record is usable only if it has provenance and was available by T-12h.
    # The target match kickoff is the time anchor; rows observed later are excluded.
    temporal = {}
    for table, key in (("injuries", "match_id"), ("lineup_projection", "match_id"),
                       ("player_features", "match_id")):
        if not table_exists(con, table):
            temporal[table] = {"eligible_rows": 0, "reason": "missing_table"}
            continue
        cols = columns(con, table)
        needed = {"match_id", "data_cutoff", "source_status"}
        if not needed.issubset(cols):
            temporal[table] = {"eligible_rows": 0, "reason": "missing_temporal_provenance"}
            continue
        statuses = ("verified", "confirmed", "valid", "success", "ok", "available")
        placeholders = ",".join("?" for _ in statuses)
        # Require the feature cutoff no later than kickoff minus configured hours.
        eligible_sql = f"""
          SELECT COUNT(*) FROM "{table}" f
          JOIN matches m ON m.match_id=f.match_id
          WHERE f.data_cutoff IS NOT NULL
            AND datetime(f.data_cutoff) <= datetime(m.kickoff, ?)
            AND lower(trim(COALESCE(f.source_status,''))) IN ({placeholders})
        """
        temporal[table] = {
            "eligible_rows": scalar(con, eligible_sql,
                (f"-{CUTOFF_HOURS} hours", *statuses)) or 0,
            "cutoff_rule": f"data_cutoff <= kickoff - {CUTOFF_HOURS} hours",
            "accepted_source_status": list(statuses),
        }

    report = {
        "audit_version": "v5.2-feature-ingestion-audit-1.0",
        "database": os.path.basename(DB),
        "cutoff_hours_before_kickoff": CUTOFF_HOURS,
        "match_rows": match_count,
        "finished_matches_with_scores": finished,
        "feature_tables": audits,
        "temporal_eligibility": temporal,
        "interpretation": {
            "schema_presence_is_not_ingestion": True,
            "nonzero_rows_do_not_prove_model_usage": True,
            "feature_effect_requires_same_sample_ablation": True,
            "v5_1_modified": False,
        },
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    con.close()


if __name__ == "__main__":
    main()
