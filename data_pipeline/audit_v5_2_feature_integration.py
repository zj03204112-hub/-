#!/usr/bin/env python3
"""Read-only feature integration audit for V5.2.

This reports what is present in the local SQLite database. It does not alter
rows or claim that a feature is used by the prediction model.
"""
import json
import os
import sqlite3
from pathlib import Path

DB = os.environ.get("FOOTBALL_DB", "football_model_database.sqlite")
OUT = os.environ.get("FEATURE_AUDIT_OUT", "data/v5_2_feature_integration_audit.json")

def main():
    if not Path(DB).exists():
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    report = {"database": DB, "tables": {}, "warnings": []}

    def count(sql, args=()):
        return int(con.execute(sql, args).fetchone()[0])

    if "matches" not in tables:
        raise SystemExit("MATCHES_TABLE_MISSING")
    report["tables"]["matches"] = {"rows": count("SELECT COUNT(*) FROM matches")}
    completed = count("SELECT COUNT(*) FROM matches m JOIN results r ON r.match_id=m.match_id WHERE r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL")
    report["completed_matches"] = completed

    specs = {
        "team_features": ["xg_for", "xg_against"],
        "injuries": ["data_cutoff", "source_status", "availability_prob", "expected_start_prob", "impact_attack", "impact_defense"],
        "player_match_stats": ["xg", "xa", "minutes_played", "data_source"],
        "player_features": ["attack90", "defense90", "influence90", "start_prob", "data_cutoff", "source_status"],
        "lineup_projection": ["attack_delta", "defense_delta", "data_cutoff", "source_status"],
        "prediction_snapshot": ["snapshot_time", "data_cutoff", "model_confidence"],
    }
    for table, cols in specs.items():
        if table not in tables:
            report["tables"][table] = {"exists": False}
            report["warnings"].append(f"{table}: table missing")
            continue
        actual = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        missing = [c for c in cols if c not in actual]
        info = {"exists": True, "columns_missing": missing}
        if missing:
            report["tables"][table] = info
            continue
        info["rows"] = count(f"SELECT COUNT(*) FROM {table}")
        if table == "team_features":
            info["xg_for_nonnull"] = count("SELECT COUNT(*) FROM team_features WHERE xg_for IS NOT NULL")
            info["xg_against_nonnull"] = count("SELECT COUNT(*) FROM team_features WHERE xg_against IS NOT NULL")
            info["matches_with_any_xg"] = count("SELECT COUNT(DISTINCT match_id) FROM team_features WHERE xg_for IS NOT NULL OR xg_against IS NOT NULL")
        elif table == "injuries":
            info["matches_covered"] = count("SELECT COUNT(DISTINCT match_id) FROM injuries")
            info["rows_with_cutoff"] = count("SELECT COUNT(*) FROM injuries WHERE data_cutoff IS NOT NULL")
            info["rows_with_source_status"] = count("SELECT COUNT(*) FROM injuries WHERE source_status IS NOT NULL AND source_status NOT IN ('','pending')")
            info["rows_with_any_impact"] = count("SELECT COUNT(*) FROM injuries WHERE impact_attack IS NOT NULL OR impact_defense IS NOT NULL")
        elif table == "player_match_stats":
            info["matches_covered"] = count("SELECT COUNT(DISTINCT match_id) FROM player_match_stats")
            info["xg_nonnull"] = count("SELECT COUNT(*) FROM player_match_stats WHERE xg IS NOT NULL")
            info["xa_nonnull"] = count("SELECT COUNT(*) FROM player_match_stats WHERE xa IS NOT NULL")
            info["xg_nonzero"] = count("SELECT COUNT(*) FROM player_match_stats WHERE xg IS NOT NULL AND xg != 0")
            info["xa_nonzero"] = count("SELECT COUNT(*) FROM player_match_stats WHERE xa IS NOT NULL AND xa != 0")
            info["sources"] = [dict(r) for r in con.execute("SELECT data_source,COUNT(*) AS rows FROM player_match_stats GROUP BY data_source ORDER BY rows DESC")]
        elif table == "player_features":
            info["matches_covered"] = count("SELECT COUNT(DISTINCT match_id) FROM player_features")
            info["rows_with_cutoff"] = count("SELECT COUNT(*) FROM player_features WHERE data_cutoff IS NOT NULL")
            info["rows_with_source_status"] = count("SELECT COUNT(*) FROM player_features WHERE source_status IS NOT NULL AND source_status NOT IN ('','pending')")
        elif table == "lineup_projection":
            info["matches_covered"] = count("SELECT COUNT(DISTINCT match_id) FROM lineup_projection")
            info["nonzero_attack_delta"] = count("SELECT COUNT(*) FROM lineup_projection WHERE attack_delta IS NOT NULL AND attack_delta != 0")
            info["nonzero_defense_delta"] = count("SELECT COUNT(*) FROM lineup_projection WHERE defense_delta IS NOT NULL AND defense_delta != 0")
            info["sources"] = [dict(r) for r in con.execute("SELECT source_status,COUNT(*) AS rows FROM lineup_projection GROUP BY source_status ORDER BY rows DESC")]
        elif table == "prediction_snapshot":
            info["matches_covered"] = count("SELECT COUNT(DISTINCT match_id) FROM prediction_snapshot")
        report["tables"][table] = info

    # Static integration claims are explicit: the currently inspected classifier
    # reads lineup_projection deltas but does not directly query injuries,
    # player_features, player_match_stats, or team_features xG fields.
    report["static_model_integration"] = {
        "lineup_projection_attack_defense_delta": "read by data_pipeline/compare_models.py:model_lambdas",
        "injuries_directly_read_by_classifier": False,
        "player_features_directly_read_by_classifier": False,
        "player_match_stats_directly_read_by_classifier": False,
        "team_features_xg_xga_directly_read_by_classifier": False,
        "note": "Static claim based on repository code audit; re-check after model code changes."
    }
    report["warnings"].append("Non-null xG/xA counts alone do not prove values are real observations; inspect source and missing-value semantics.")
    report["warnings"].append("This is a read-only data coverage report, not a model backtest.")
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    con.close()

if __name__ == "__main__":
    main()
