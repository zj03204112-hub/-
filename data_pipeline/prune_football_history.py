#!/usr/bin/env python3
"""Prune football match data older than the rolling three-year retention window.

The workflow stores a pre-cleanup database backup as an artifact. Historical
prediction snapshots and provider ID mappings are intentionally preserved for
traceability; active match/result/player/statistical rows older than cutoff are
removed. The script is transactional and emits a JSON audit report.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("FOOTBALL_DB_PATH", "football_model_database.sqlite"))
AS_OF = date.fromisoformat(os.environ.get("RETENTION_AS_OF", date.today().isoformat()))
CUTOFF = AS_OF - timedelta(days=365 * 3)
REPORT_PATH = Path(os.environ.get("RETENTION_REPORT", "retention_cleanup_report.json"))
BACKUP_PATH = Path(os.environ.get("RETENTION_BACKUP", "/tmp/football_model_database_pre_retention.sqlite"))

PRESERVE_TABLES = {"prediction_snapshot", "provider_event_map"}
# Only delete rows from tables whose match_id can be traced to the old match set.
# Tables without match_id are left alone because their date semantics are unknown.

def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit(f"Database not found: {DB_PATH}")

    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_PATH, BACKUP_PATH)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        integrity_before = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity_before != "ok":
            raise RuntimeError(f"Pre-cleanup integrity_check failed: {integrity_before}")

        tables = {
            row["name"] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "matches" not in tables or "kickoff" not in {
            row["name"] for row in con.execute("PRAGMA table_info(matches)")
        }:
            raise RuntimeError("Expected matches table with kickoff column was not found")

        old_ids = [
            row[0] for row in con.execute(
                "SELECT match_id FROM matches WHERE date(kickoff) < date(?)",
                (CUTOFF.isoformat(),),
            )
        ]
        old_id_set = set(old_ids)
        before = {
            "matches": con.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
            "old_matches": len(old_ids),
        }
        deleted_by_table: dict[str, int] = {}

        con.execute("BEGIN IMMEDIATE")
        # Delete child records first, but leave historical predictions and provider mappings intact.
        for table in sorted(tables):
            if table in {"matches", "results"} or table in PRESERVE_TABLES:
                continue
            cols = {row["name"] for row in con.execute(f'PRAGMA table_info("{table}")')}
            if "match_id" not in cols:
                continue
            cur = con.execute(
                f'DELETE FROM "{table}" WHERE match_id IN '
                f'(SELECT match_id FROM matches WHERE date(kickoff) < date(?))',
                (CUTOFF.isoformat(),),
            )
            if cur.rowcount:
                deleted_by_table[table] = cur.rowcount

        cur = con.execute(
            "DELETE FROM results WHERE match_id IN "
            "(SELECT match_id FROM matches WHERE date(kickoff) < date(?))",
            (CUTOFF.isoformat(),),
        )
        if cur.rowcount:
            deleted_by_table["results"] = cur.rowcount

        cur = con.execute(
            "DELETE FROM matches WHERE date(kickoff) < date(?)",
            (CUTOFF.isoformat(),),
        )
        deleted_by_table["matches"] = cur.rowcount

        con.execute("COMMIT")
        integrity_after = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity_after != "ok":
            raise RuntimeError(f"Post-cleanup integrity_check failed: {integrity_after}")

        after = {
            "matches": con.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
            "results": con.execute("SELECT COUNT(*) FROM results").fetchone()[0] if "results" in tables else None,
            "matches_before_cutoff_remaining": con.execute(
                "SELECT COUNT(*) FROM matches WHERE date(kickoff) < date(?)", (CUTOFF.isoformat(),)
            ).fetchone()[0],
            "preserved_prediction_snapshots": con.execute(
                "SELECT COUNT(*) FROM prediction_snapshot"
            ).fetchone()[0] if "prediction_snapshot" in tables else None,
            "preserved_provider_event_mappings": con.execute(
                "SELECT COUNT(*) FROM provider_event_map"
            ).fetchone()[0] if "provider_event_map" in tables else None,
        }
        report = {
            "status": "success",
            "as_of_date": AS_OF.isoformat(),
            "retention_years": 3,
            "cutoff_exclusive": CUTOFF.isoformat(),
            "retained_kickoff_rule": f"date(kickoff) >= {CUTOFF.isoformat()}",
            "before": before,
            "deleted_rows_by_table": deleted_by_table,
            "after": after,
            "integrity_check_before": integrity_before,
            "integrity_check_after": integrity_after,
            "backup_path": str(BACKUP_PATH),
            "notes": [
                "Historical prediction_snapshot and provider_event_map rows were preserved for traceability.",
                "Tables without match_id were not pruned because their match-level date linkage is unknown.",
                "Rows exactly on the cutoff date are retained."
            ],
        }
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        # Restore original database if any post-commit validation failed.
        if BACKUP_PATH.exists():
            con.close()
            shutil.copy2(BACKUP_PATH, DB_PATH)
            raise
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
