#!/usr/bin/env python3
"""Read-only coverage audit for V5.2. Does not modify the database."""
import json
import os
import sqlite3
from pathlib import Path

DB = os.getenv("FOOTBALL_DB", "football_model_database.sqlite")
OUT = os.getenv("FEATURE_AUDIT_OUT", "data/v52_feature_coverage.json")

def table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def count(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]

def main():
    if not Path(DB).exists():
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    tables = ["matches", "results", "team_features", "injuries", "player_match_stats",
              "player_features", "lineup_projection", "prediction_snapshot", "review"]
    existing = {t: table_exists(con, t) for t in tables}
    out = {"database": DB, "read_only": True, "tables": existing, "coverage": {}}
    if not existing["matches"]:
        raise SystemExit("MATCHES_TABLE_MISSING")
    n_matches = count(con, "SELECT COUNT(*) FROM matches")
    out["matches_total"] = n_matches
    if existing["results"]:
        out["finished_matches"] = count(con, "SELECT COUNT(*) FROM results WHERE ft_home IS NOT NULL AND ft_away IS NOT NULL")
    if existing["team_features"]:
        out["coverage"]["team_features"] = {
            "rows": count(con, "SELECT COUNT(*) FROM team_features"),
            "unique_matches": count(con, "SELECT COUNT(DISTINCT match_id) FROM team_features"),
            "xg_for_nonnull": count(con, "SELECT COUNT(*) FROM team_features WHERE xg_for IS NOT NULL"),
            "xga_nonnull": count(con, "SELECT COUNT(*) FROM team_features WHERE xg_against IS NOT NULL"),
            "xg_for_positive": count(con, "SELECT COUNT(*) FROM team_features WHERE xg_for>0"),
            "xga_positive": count(con, "SELECT COUNT(*) FROM team_features WHERE xg_against>0"),
        }
    if existing["injuries"]:
        out["coverage"]["injuries"] = {
            "rows": count(con, "SELECT COUNT(*) FROM injuries"),
            "unique_matches": count(con, "SELECT COUNT(DISTINCT match_id) FROM injuries"),
            "with_source_url": count(con, "SELECT COUNT(*) FROM injuries WHERE source_url IS NOT NULL AND source_url<>''"),
            "with_data_cutoff": count(con, "SELECT COUNT(*) FROM injuries WHERE data_cutoff IS NOT NULL"),
            "with_observed_at": count(con, "SELECT COUNT(*) FROM injuries WHERE observed_at IS NOT NULL"),
            "nonpending_source": count(con, "SELECT COUNT(*) FROM injuries WHERE source_status IS NOT NULL AND source_status NOT IN ('pending','unknown')"),
        }
    if existing["player_match_stats"]:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(player_match_stats)")}
        out["coverage"]["player_match_stats"] = {
            "rows": count(con, "SELECT COUNT(*) FROM player_match_stats"),
            "unique_matches": count(con, "SELECT COUNT(DISTINCT match_id) FROM player_match_stats"),
            "unique_players": count(con, "SELECT COUNT(DISTINCT player_id) FROM player_match_stats"),
            "sources": [dict(r) for r in con.execute("SELECT data_source,COUNT(*) AS rows FROM player_match_stats GROUP BY data_source ORDER BY rows DESC")],
            "xg_nonzero": count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xg>0"),
            "xa_nonzero": count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xa>0"),
            "xg_zero": count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xg=0"),
            "xa_zero": count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xa=0"),
            "availability_markers_present": ("xg_source_available" in cols and "xa_source_available" in cols),
        }
        if "xg_source_available" in cols:
            out["coverage"]["player_match_stats"]["xg_source_available_rows"] = count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xg_source_available=1")
        if "xa_source_available" in cols:
            out["coverage"]["player_match_stats"]["xa_source_available_rows"] = count(con, "SELECT COUNT(*) FROM player_match_stats WHERE xa_source_available=1")
    if existing["player_features"]:
        out["coverage"]["player_features"] = {
            "rows": count(con, "SELECT COUNT(*) FROM player_features"),
            "unique_matches": count(con, "SELECT COUNT(DISTINCT match_id) FROM player_features"),
            "with_sample_minutes": count(con, "SELECT COUNT(*) FROM player_features WHERE sample_minutes>0"),
            "source_status": [dict(r) for r in con.execute("SELECT source_status,COUNT(*) AS rows FROM player_features GROUP BY source_status")],
        }
    if existing["lineup_projection"]:
        out["coverage"]["lineup_projection"] = {
            "rows": count(con, "SELECT COUNT(*) FROM lineup_projection"),
            "unique_matches": count(con, "SELECT COUNT(DISTINCT match_id) FROM lineup_projection"),
            "nonzero_attack_delta": count(con, "SELECT COUNT(*) FROM lineup_projection WHERE ABS(attack_delta)>0"),
            "nonzero_defense_delta": count(con, "SELECT COUNT(*) FROM lineup_projection WHERE ABS(defense_delta)>0"),
            "with_player_count": count(con, "SELECT COUNT(*) FROM lineup_projection WHERE player_count>0"),
            "source_status": [dict(r) for r in con.execute("SELECT source_status,COUNT(*) AS rows FROM lineup_projection GROUP BY source_status")],
        }
    if existing["prediction_snapshot"]:
        out["coverage"]["prediction_snapshot"] = {
            "rows": count(con, "SELECT COUNT(*) FROM prediction_snapshot"),
            "unique_matches": count(con, "SELECT COUNT(DISTINCT match_id) FROM prediction_snapshot"),
            "with_frozen_cutoff": count(con, "SELECT COUNT(*) FROM prediction_snapshot WHERE data_cutoff IS NOT NULL AND data_cutoff<>''"),
        }
    if existing["review"]:
        out["coverage"]["review"] = {"rows": count(con, "SELECT COUNT(*) FROM review")}
    con.close()
    out["warnings"] = [
        "Coverage means rows exist; it does not prove the prediction code consumed them.",
        "Player xG/xA zeros are ambiguous unless source-availability flags are present.",
        "Static code audit found current compare_models.model_lambdas reads lineup_projection deltas but does not directly query team_features.xg_for/xg_against, injuries, player_match_stats, or player_features.",
        "Do not report data as model-used until the model run log confirms feature joins and nonzero/valid feature counts."
    ]
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
