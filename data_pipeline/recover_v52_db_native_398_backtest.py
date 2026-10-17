#!/usr/bin/env python3
"""Recover the V5.2 DB-native 2026-09-01..2026-10-13 row-level backtest from the checked-in SQLite DB.
Read-only source DB; exports match/result fields plus the latest stored prediction_snapshot per match.
Fails closed unless the match/result cohort is exactly the documented 398 unique match IDs.
"""
import csv, json, sqlite3
from pathlib import Path

DB = Path("football_model_database.sqlite")
OUT = Path("model_validation/v52_jincai_20260901_20261013/v52_db_native_predictions_backtest_20260901_20261013.csv")
START, END = "2026-09-01", "2026-10-13"
EXPECTED = 398

def qi(s):
    return '"' + s.replace('"', '""') + '"'

def cols(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({qi(table)})")]

def main():
    if not DB.exists():
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"matches", "results"}.issubset(tables):
        raise SystemExit("REQUIRED_TABLES_MISSING")
    mc, rc = cols(con, "matches"), cols(con, "results")
    if not {"match_id", "kickoff", "home_team", "away_team"}.issubset(mc):
        raise SystemExit(f"MATCH_COLUMNS_UNEXPECTED: {mc}")
    if not {"match_id", "ft_home", "ft_away"}.issubset(rc):
        raise SystemExit(f"RESULT_COLUMNS_UNEXPECTED: {rc}")
    select = [f'm.{qi(c)} AS {qi("match_"+c)}' for c in mc]
    select += [f'r.{qi(c)} AS {qi("result_"+c)}' for c in rc if c != "match_id"]
    joins = ""
    params = [START, END]
    snapshot_cols = []
    if "prediction_snapshot" in tables:
        sc = cols(con, "prediction_snapshot")
        if "match_id" in sc:
            order_col = next((c for c in ("data_cutoff", "created_at", "snapshot_at", "updated_at", "id") if c in sc), None)
            if order_col:
                snapshot_cols = sc
                joins = f"""
                LEFT JOIN (
                  SELECT * FROM (
                    SELECT ps.*, ROW_NUMBER() OVER (
                      PARTITION BY match_id ORDER BY {qi(order_col)} DESC
                    ) AS __rn
                    FROM prediction_snapshot ps
                  ) WHERE __rn=1
                ) p ON p.match_id=m.match_id
                """
                select += [f'p.{qi(c)} AS {qi("snapshot_"+c)}' for c in sc if c != "match_id"]
    if not snapshot_cols:
        select.append("NULL AS snapshot_recovery_status")
    sql = f"""
      SELECT {', '.join(select)}
      FROM matches m JOIN results r ON r.match_id=m.match_id
      {joins}
      WHERE substr(replace(m.kickoff,'T',' '),1,10) BETWEEN ? AND ?
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
      ORDER BY m.kickoff, m.match_id
    """
    rows = [dict(r) for r in con.execute(sql, params)]
    con.close()
    ids = [r.get("match_match_id") for r in rows]
    if len(rows) != EXPECTED or len(set(ids)) != EXPECTED or any(not x for x in ids):
        raise SystemExit(json.dumps({
          "status":"FAILED_COHORT_VALIDATION", "expected_rows":EXPECTED,
          "actual_rows":len(rows), "unique_match_ids":len(set(ids)),
          "first_dates":[r.get("match_kickoff") for r in rows[:3]],
          "last_dates":[r.get("match_kickoff") for r in rows[-3:]]
        }, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({
      "status":"EXPORTED", "rows":len(rows), "unique_match_ids":len(set(ids)),
      "date_range":[START,END], "prediction_snapshot_table_present":bool(snapshot_cols),
      "snapshot_columns_exported":len(snapshot_cols),
      "output":str(OUT),
      "note":"Raw stored prediction snapshots are exported as-is; no predictions are reconstructed or overwritten."
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
