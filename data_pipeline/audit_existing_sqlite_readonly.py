"""Read-only audit of the repository SQLite database. Does not modify the database."""
# W45 scheduled weekly audit trigger; database access remains read-only.
import json, os, sqlite3, hashlib
from pathlib import Path
from datetime import datetime, timezone

DB = Path(os.environ.get("FOOTBALL_DB_PATH", "football_model_database.sqlite"))
OUT = Path(os.environ.get("AUDIT_OUT", "database_audit_report.json"))

def main():
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "database_path": str(DB),
        "exists": DB.is_file(),
        "size_bytes": DB.stat().st_size if DB.is_file() else None,
        "sha256": hashlib.sha256(DB.read_bytes()).hexdigest() if DB.is_file() else None,
        "tables": [], "league_summary": [], "duplicate_checks": [],
        "limitations": []
    }
    if not DB.is_file() or DB.stat().st_size == 0:
        report["status"] = "BLOCKED_DATABASE_MISSING_OR_EMPTY"
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    con = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        report["integrity_check"] = integrity
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tables:
            cols = [dict(r) for r in con.execute(f'PRAGMA table_info("{t}")')]
            count = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            report["tables"].append({"name": t, "rows": count, "columns": [c["name"] for c in cols]})
        match_candidates = []
        for item in report["tables"]:
            names = {c.lower() for c in item["columns"]}
            if ({"hometeam","awayteam"} <= names or {"home_team","away_team"} <= names
                or {"home_team_name","away_team_name"} <= names):
                match_candidates.append(item)
        report["match_table_candidates"] = match_candidates
        for item in match_candidates:
            t = item["name"]
            cols = item["columns"]
            lower = {c.lower(): c for c in cols}
            league_col = next((lower[k] for k in ("league","league_name","competition","competition_name","league_code") if k in lower), None)
            home_col = next((lower[k] for k in ("hometeam","home_team","home_team_name") if k in lower), None)
            away_col = next((lower[k] for k in ("awayteam","away_team","away_team_name") if k in lower), None)
            hg_col = next((lower[k] for k in ("fthg","home_goals","home_score","ft_home","score_home") if k in lower), None)
            ag_col = next((lower[k] for k in ("ftag","away_goals","away_score","ft_away","score_away") if k in lower), None)
            id_col = next((lower[k] for k in ("match_id","id","fixture_id","event_id") if k in lower), None)
            if league_col:
                sql = f'SELECT "{league_col}" league, COUNT(*) total'
                if hg_col and ag_col:
                    sql += f', SUM(CASE WHEN "{hg_col}" IS NOT NULL AND "{ag_col}" IS NOT NULL THEN 1 ELSE 0 END) results_complete'
                else:
                    sql += ', NULL results_complete'
                sql += f' FROM "{t}" GROUP BY "{league_col}" ORDER BY total DESC'
                for row in con.execute(sql):
                    total = row["total"]
                    complete = row["results_complete"]
                    report["league_summary"].append({
                        "table": t, "league": row["league"], "matches": total,
                        "results_complete": complete,
                        "result_completeness_pct": round(100*complete/total, 2) if complete is not None and total else None
                    })
            if id_col:
                dup = con.execute(f'SELECT "{id_col}" key, COUNT(*) n FROM "{t}" GROUP BY "{id_col}" HAVING COUNT(*)>1 LIMIT 100').fetchall()
                report["duplicate_checks"].append({"table": t, "key": id_col, "duplicate_key_groups": len(dup), "examples": [dict(x) for x in dup]})
            if home_col and away_col:
                dup = con.execute(f'SELECT "{home_col}" home, "{away_col}" away, COUNT(*) n FROM "{t}" GROUP BY "{home_col}","{away_col}" HAVING COUNT(*)>1 LIMIT 100').fetchall()
                report["duplicate_checks"].append({"table": t, "key": [home_col, away_col], "duplicate_pair_groups": len(dup), "examples": [dict(x) for x in dup]})
        if not match_candidates:
            report["limitations"].append("No obvious match table found by column names; manual schema mapping required.")
        if not report["league_summary"]:
            report["limitations"].append("Could not infer league column and embedded score columns together; result joins require manual schema mapping.")
        # W44 scope query: read-only, using the stored kickoff date as recorded.
        wstart, wend = "2026-10-26", "2026-11-01"
        def scalar(sql, params=()):
            try:
                return con.execute(sql, params).fetchone()[0]
            except sqlite3.Error as exc:
                return {"query_error": str(exc)}
        matches_min = scalar("SELECT MIN(kickoff) FROM matches")
        matches_max = scalar("SELECT MAX(kickoff) FROM matches")
        results_min = scalar("SELECT MIN(completed_at) FROM results")
        results_max = scalar("SELECT MAX(completed_at) FROM results")
        pred_min = scalar("SELECT MIN(snapshot_time) FROM prediction_snapshot")
        pred_max = scalar("SELECT MAX(snapshot_time) FROM prediction_snapshot")
        w44_matches = scalar("SELECT COUNT(*) FROM matches WHERE substr(kickoff,1,10) BETWEEN ? AND ?", (wstart, wend))
        w44_results = scalar("SELECT COUNT(*) FROM results r JOIN matches m ON m.match_id=r.match_id WHERE substr(m.kickoff,1,10) BETWEEN ? AND ? AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL", (wstart, wend))
        w44_predictions = scalar("SELECT COUNT(*) FROM prediction_snapshot p JOIN matches m ON m.match_id=p.match_id WHERE substr(m.kickoff,1,10) BETWEEN ? AND ?", (wstart, wend))
        w44_markets = scalar("SELECT COUNT(*) FROM sporttery_market s JOIN matches m ON m.match_id=s.match_id WHERE substr(m.kickoff,1,10) BETWEEN ? AND ?", (wstart, wend))
        pred_cols = next((x["columns"] for x in report["tables"] if x["name"] == "prediction_snapshot"), [])
        xg_candidates = []
        for item in report["tables"]:
            cols = {c.lower() for c in item["columns"]}
            if "match_id" in cols and any(c in cols for c in ("xg","xga","xg_for","xg_against","squad_xg90")):
                role = "PLAYER_LEVEL" if item["name"] == "player_match_stats" else ("AGGREGATED_FEATURE" if item["name"] in ("team_features","advanced_team_features") else "UNCLASSIFIED")
                xg_candidates.append({"table": item["name"], "rows": item["rows"], "columns": item["columns"], "role": role})
        report["w44_database_scope"] = {
            "iso_week": "2026-W44", "week_start": wstart, "week_end": wend,
            "matches": w44_matches, "completed_results_with_ft_score": w44_results,
            "prediction_snapshot_rows": w44_predictions, "sporttery_market_rows": w44_markets,
            "database_match_kickoff_min": matches_min, "database_match_kickoff_max": matches_max,
            "database_result_completed_at_min": results_min, "database_result_completed_at_max": results_max,
            "prediction_snapshot_time_min": pred_min, "prediction_snapshot_time_max": pred_max
        }
        report["prediction_snapshot_capability"] = {
            "columns": pred_cols,
            "model_version_field": "model_version" in pred_cols,
            "ninety_minute_probability_fields": [c for c in pred_cols if any(k in c.lower() for k in ("prob","home_win","draw","away_win"))],
            "lambda_fields": [c for c in pred_cols if "lambda" in c.lower()],
            "git_sha_field": any("sha" in c.lower() for c in pred_cols),
            "sporttery_handicap_field": "handicap" in pred_cols,
            "score1_field": "score_1" in pred_cols,
            "score2_field": "score_2" in pred_cols
        }
        report["xg_table_candidates"] = xg_candidates
        report["limitations"].append("The database prediction_snapshot table has no model_version, independent 90-minute probability, lambda or Git SHA fields; the table cannot support version-stratified Brier/Log Loss.")
        report["limitations"].append("player_match_stats.xg is player-level; team_features.xg_for/xg_against and advanced_team_features.squad_xg90 are feature aggregates, not supplier-published match-level team xG observations.")
        report["status"] = "AUDIT_COMPLETE_READ_ONLY" if integrity == "ok" else "DATABASE_INTEGRITY_WARNING"
    finally:
        con.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
