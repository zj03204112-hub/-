#!/usr/bin/env python3
"""Read-only match/result/prediction/review reconciliation for V5.2."""
import csv
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

DB = os.getenv("FOOTBALL_DB", "football_model_database.sqlite")
OUT = Path(os.getenv("ARCHIVE_AUDIT_OUT", "data/v52_archive_chain_audit.json"))
DETAIL_DIR = Path(os.getenv("ARCHIVE_AUDIT_DETAILS", "data/v52_archive_chain_details"))


def exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def cols(con, table):
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    if not Path(DB).exists():
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}")
    con = sqlite3.connect(f"file:{Path(DB).resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    required = {"matches", "results", "prediction_snapshot", "review"}
    present = {t: exists(con, t) for t in required}
    missing_tables = sorted(t for t, ok in present.items() if not ok)
    if missing_tables:
        raise SystemExit("REQUIRED_TABLES_MISSING: " + ",".join(missing_tables))

    match_cols = cols(con, "matches")
    result_cols = cols(con, "results")
    snap_cols = cols(con, "prediction_snapshot")
    review_cols = cols(con, "review")
    if not {"match_id", "kickoff", "home_team", "away_team", "status"}.issubset(match_cols):
        raise SystemExit("MATCHES_SCHEMA_UNEXPECTED")
    if not {"match_id", "ft_home", "ft_away"}.issubset(result_cols):
        raise SystemExit("RESULTS_SCHEMA_UNEXPECTED")
    if not {"match_id", "snapshot_time", "data_cutoff"}.issubset(snap_cols):
        raise SystemExit("SNAPSHOT_SCHEMA_UNEXPECTED")
    if "match_id" not in review_cols:
        raise SystemExit("REVIEW_SCHEMA_UNEXPECTED")

    matches = {r["match_id"]: dict(r) for r in con.execute(
        "SELECT match_id,kickoff,home_team,away_team,status FROM matches"
    )}
    results = {r["match_id"]: dict(r) for r in con.execute(
        "SELECT match_id,ft_home,ft_away,result_1x2,completed_at,source_status FROM results"
    )}
    snapshots = [dict(r) for r in con.execute(
        "SELECT * FROM prediction_snapshot ORDER BY match_id,snapshot_time,prediction_id"
        if "prediction_id" in snap_cols else
        "SELECT * FROM prediction_snapshot ORDER BY match_id,snapshot_time"
    )]
    reviews = [dict(r) for r in con.execute("SELECT * FROM review ORDER BY match_id")]

    snapshot_by_match = {}
    for row in snapshots:
        snapshot_by_match.setdefault(row["match_id"], []).append(row)
    review_by_match = {}
    for row in reviews:
        review_by_match.setdefault(row["match_id"], []).append(row)

    match_ids, result_ids, snapshot_ids, review_ids = (
        set(matches), set(results), set(snapshot_by_match), set(review_by_match)
    )
    finished_ids = {mid for mid, row in matches.items() if row.get("status") == "finished"}
    scored_ids = {mid for mid, row in results.items()
                  if row.get("ft_home") is not None and row.get("ft_away") is not None}
    finished_scored = finished_ids & scored_ids

    no_result = sorted(finished_ids - scored_ids)
    result_without_match = sorted(result_ids - match_ids)
    snapshot_without_match = sorted(snapshot_ids - match_ids)
    finished_without_snapshot = sorted(finished_scored - snapshot_ids)
    snapshot_without_result = sorted(snapshot_ids - scored_ids)
    review_without_match = sorted(review_ids - match_ids)
    review_without_snapshot = sorted(review_ids - snapshot_ids)
    finished_without_review = sorted(finished_scored - review_ids)
    duplicate_snapshot_matches = sorted(mid for mid, rows in snapshot_by_match.items() if len(rows) > 1)

    cutoff_missing = 0
    cutoff_parse_errors = 0
    cutoff_late = []
    for mid, rows in snapshot_by_match.items():
        match = matches.get(mid)
        kickoff = parse_dt(match.get("kickoff")) if match else None
        if not kickoff:
            continue
        cutoff_limit = kickoff - timedelta(hours=12)
        for row in rows:
            raw = row.get("data_cutoff")
            cutoff = parse_dt(raw)
            if not raw:
                cutoff_missing += 1
            elif not cutoff:
                cutoff_parse_errors += 1
            else:
                # Compare aware timestamps safely; preserve the database's recorded timezone.
                if kickoff.tzinfo is None and cutoff.tzinfo is not None:
                    cutoff = cutoff.replace(tzinfo=None)
                elif kickoff.tzinfo is not None and cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=kickoff.tzinfo)
                if cutoff > cutoff_limit:
                    cutoff_late.append({
                        "match_id": mid, "kickoff": match.get("kickoff"),
                        "prediction_id": row.get("prediction_id"),
                        "snapshot_time": row.get("snapshot_time"), "data_cutoff": raw,
                    })

    summary = {
        "database": DB,
        "read_only": True,
        "tables_present": present,
        "counts": {
            "matches": len(matches),
            "finished_matches": len(finished_ids),
            "results_rows": len(results),
            "scored_result_matches": len(scored_ids),
            "finished_matches_with_scores": len(finished_scored),
            "prediction_snapshot_rows": len(snapshots),
            "prediction_snapshot_unique_matches": len(snapshot_ids),
            "review_rows": len(reviews),
            "review_unique_matches": len(review_ids),
            "matches_with_multiple_snapshots": len(duplicate_snapshot_matches),
        },
        "reconciliation": {
            "finished_without_score_count": len(no_result),
            "results_without_match_count": len(result_without_match),
            "snapshot_match_ids_not_in_matches_count": len(snapshot_without_match),
            "finished_scored_without_snapshot_count": len(finished_without_snapshot),
            "snapshots_without_scored_result_count": len(snapshot_without_result),
            "reviews_without_match_count": len(review_without_match),
            "reviews_without_snapshot_count": len(review_without_snapshot),
            "finished_scored_without_review_count": len(finished_without_review),
            "snapshot_cutoff_missing_rows": cutoff_missing,
            "snapshot_cutoff_unparseable_rows": cutoff_parse_errors,
            "snapshot_cutoff_later_than_kickoff_minus_12h_count": len(cutoff_late),
        },
        "details": {},
        "warnings": [
            "A snapshot row is not automatically proof it was the exact pre-match prediction delivered to the user.",
            "Multiple snapshots for one match must be resolved by model version and freeze time, not silently deduplicated.",
            "Review rows may legitimately be missing for matches not yet reviewed; counts are reconciliation targets, not automatic failures.",
            "This script does not modify the database or historical predictions."
        ]
    }

    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    detail_sets = {
        "finished_matches_without_score.csv": [
            {"match_id": mid, **matches[mid]} for mid in no_result if mid in matches
        ],
        "results_without_match.csv": [{"match_id": mid, **results[mid]} for mid in result_without_match if mid in results],
        "snapshot_ids_not_in_matches.csv": [{"match_id": mid, "snapshot_count": len(snapshot_by_match[mid])} for mid in snapshot_without_match],
        "finished_scored_without_snapshot.csv": [
            {"match_id": mid, **matches[mid], "ft_home": results[mid].get("ft_home"), "ft_away": results[mid].get("ft_away")}
            for mid in finished_without_snapshot if mid in matches and mid in results
        ],
        "snapshots_without_scored_result.csv": [
            {"match_id": mid, "snapshot_count": len(snapshot_by_match[mid])} for mid in snapshot_without_result
        ],
        "reviews_without_match.csv": [{"match_id": mid, "review_count": len(review_by_match[mid])} for mid in review_without_match],
        "reviews_without_snapshot.csv": [{"match_id": mid, "review_count": len(review_by_match[mid])} for mid in review_without_snapshot],
        "finished_scored_without_review.csv": [
            {"match_id": mid, **matches[mid], "ft_home": results[mid].get("ft_home"), "ft_away": results[mid].get("ft_away")}
            for mid in finished_without_review if mid in matches and mid in results
        ],
        "multiple_snapshots_per_match.csv": [
            {"match_id": mid, "snapshot_count": len(snapshot_by_match[mid]),
             "snapshot_times": " ; ".join(str(r.get("snapshot_time","")) for r in snapshot_by_match[mid]),
             "data_cutoffs": " ; ".join(str(r.get("data_cutoff","")) for r in snapshot_by_match[mid])}
            for mid in duplicate_snapshot_matches
        ],
        "late_snapshot_cutoffs.csv": cutoff_late,
    }
    for filename, rows in detail_sets.items():
        fields = sorted({key for row in rows for key in row}) or ["match_id"]
        write_csv(DETAIL_DIR / filename, rows, fields)
        summary["details"][filename] = {"rows": len(rows), "path": str(DETAIL_DIR / filename)}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    con.close()


if __name__ == "__main__":
    main()
