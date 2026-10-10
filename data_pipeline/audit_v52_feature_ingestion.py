#!/usr/bin/env python3
"""Read-only V5.2 feature integration audit for football_model_database.sqlite.

This script never updates the database or existing predictions. It reports row
coverage and statically documents which tables the current prediction function
actually reads. Run from repository root in the same environment as the DB.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta

DB = os.getenv("FOOTBALL_DB", "football_model_database.sqlite")
OUT = os.getenv("V52_AUDIT_OUT", "data/v52_feature_ingestion_audit.json")

TABLES = {
    "matches": ["match_id", "kickoff", "status"],
    "results": ["match_id", "ft_home", "ft_away"],
    "team_features": ["match_id", "team_side", "xg_for", "xg_against"],
    "injuries": ["match_id", "team_side", "player", "data_cutoff", "source_status",
                 "availability_prob", "expected_start_prob", "impact_attack", "impact_defense"],
    "player_match_stats": ["match_id", "player_id", "xg", "xa", "data_source", "observed_at"],
    "player_features": ["match_id", "player_id", "attack90", "defense90", "influence90", "start_prob"],
    "lineup_projection": ["match_id", "team_side", "attack_delta", "defense_delta", "data_cutoff", "source_status"],
    "prediction_snapshot": ["match_id", "model_version", "predicted_at"],
}

# Static source inspection of data_pipeline/compare_models.py:
# model_lambdas() reads match history, schedule_intent and lineup_projection.
# It does not directly query team_features, injuries, player_match_stats or
# player_features. A feature in one of those tables is not "model-used" unless
# an explicit, tested transformation feeds it into model_lambdas().
MODEL_READS = {
    "historical results / opponent-adjusted rates": "direct",
    "schedule_intent": "model-dependent; V3 path only",
    "lineup_projection.attack_delta/defense_delta": "direct if rows exist",
    "team_features.xg_for/xg_against": "not directly read by current model_lambdas",
    "injuries": "not directly read; possible indirect effect only via lineup_projection",
    "player_match_stats": "not directly read",
    "player_features": "not directly read",
}

def table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def cols(con, name):
    return {r[1] for r in con.execute(f'PRAGMA table_info("{name}")').fetchall()}

def count(con, sql, args=()):
    try:
        row = con.execute(sql, args).fetchone()
        return row[0] if row else None
    except sqlite3.Error as exc:
        return {"error": str(exc)}

def table_report(con, table):
    if not table_exists(con, table):
        return {"exists": False, "rows": 0, "missing_expected_columns": TABLES[table]}
    available = cols(con, table)
    missing = [c for c in TABLES[table] if c not in available]
    rows = count(con, f'SELECT COUNT(*) FROM "{table}"')
    report = {"exists": True, "rows": rows, "columns": sorted(available),
              "missing_expected_columns": missing}
    if table == "team_features" and {"xg_for", "xg_against"} <= available:
        report["xg_nonnull_rows"] = count(con, "SELECT COUNT(*) FROM team_features WHERE xg_for IS NOT NULL AND xg_against IS NOT NULL")
        report["xg_zero_zero_rows"] = count(con, "SELECT COUNT(*) FROM team_features WHERE xg_for=0 AND xg_against=0")
        report["xg_missing_rows"] = count(con, "SELECT COUNT(*) FROM team_features WHERE xg_for IS NULL OR xg_against IS NULL")
    elif table == "injuries" and {"match_id", "data_cutoff"} <= available:
        report["distinct_matches"] = count(con, "SELECT COUNT(DISTINCT match_id) FROM injuries")
        report["with_cutoff"] = count(con, "SELECT COUNT(*) FROM injuries WHERE data_cutoff IS NOT NULL")
        if "source_status" in available:
            report["source_status_counts"] = dict(con.execute("SELECT COALESCE(source_status,'<NULL>'),COUNT(*) FROM injuries GROUP BY source_status").fetchall())
    elif table == "player_match_stats" and {"match_id", "xg", "xa"} <= available:
        report["distinct_matches"] = count(con, "SELECT COUNT(DISTINCT match_id) FROM player_match_stats")
        report["nonnull_xg_xa_rows"] = count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xg IS NOT NULL AND xa IS NOT NULL")
        report["zero_zero_xg_xa_rows"] = count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xg=0 AND xa=0")
        report["distinct_sources"] = [r[0] for r in con.execute("SELECT DISTINCT data_source FROM player_match_stats").fetchall()] if "data_source" in available else []
    elif table == "player_features" and "match_id" in available:
        report["distinct_matches"] = count(con, "SELECT COUNT(DISTINCT match_id) FROM player_features")
    elif table == "lineup_projection" and {"match_id", "attack_delta", "defense_delta"} <= available:
        report["distinct_matches"] = count(con, "SELECT COUNT(DISTINCT match_id) FROM lineup_projection")
        report["nonzero_delta_rows"] = count(con, "SELECT COUNT(*) FROM lineup_projection WHERE COALESCE(attack_delta,0) != 0 OR COALESCE(defense_delta,0) != 0")
        if "source_status" in available:
            report["source_status_counts"] = dict(con.execute("SELECT COALESCE(source_status,'<NULL>'),COUNT(*) FROM lineup_projection GROUP BY source_status").fetchall())
    return report

def main():
    if not os.path.exists(DB):
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}. Run this audit in the environment where the real SQLite database is available.")
    con = sqlite3.connect(DB)
    try:
        result = {
            "generated_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "database_path": DB,
            "read_only": True,
            "tables": {name: table_report(con, name) for name in TABLES},
            "model_read_path_static_audit": MODEL_READS,
            "interpretation": {
                "collected": "row exists in source table",
                "verified": "source_status/provenance and time cutoff checked; row counts alone cannot establish this",
                "model_used": "must be confirmed by explicit model read path and ablation test",
            },
            "warning": "This is a read-only schema/coverage audit, not a claim that features improve predictions. Inspect collector semantics and run time-ordered ablations before enabling features."
        }
    finally:
        con.close()
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
