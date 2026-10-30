"""Read-only audit of the repository SQLite database. Does not modify the database."""
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
        # Identify likely match/result tables from schema; do not guess joins.
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
        report["status"] = "AUDIT_COMPLETE_READ_ONLY" if integrity == "ok" else "DATABASE_INTEGRITY_WARNING"
    finally:
        con.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
