#!/usr/bin/env python3
"""Import sourced, match-level xG observations into the isolated V5.2 SQLite DB.

CSV columns: match_id,team_side,xg_for,xg_against,source_url,observed_at,source_status
No xG values are inferred or filled when missing. Re-imports update only the same
(match_id, team_side, source_url) observation. V5.1 is not modified by this script.
"""
import csv
import os
import sqlite3
import sys
from datetime import datetime

DB = os.environ.get("FOOTBALL_DB", "football_model_database.sqlite")
CSV_PATH = os.environ.get("XG_CSV", "")
ALLOWED_STATUS = {"verified", "confirmed", "valid", "success", "ok", "available"}


def parse_dt(value, field):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="seconds")
    except Exception as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def main():
    if not CSV_PATH or not os.path.isfile(CSV_PATH):
        raise SystemExit("XG_CSV_NOT_FOUND: provide a sourced CSV; no synthetic values are allowed")
    con = sqlite3.connect(DB)
    con.execute("""
      CREATE TABLE IF NOT EXISTS xg_observations (
        match_id TEXT NOT NULL,
        team_side TEXT NOT NULL CHECK(team_side IN ('home','away')),
        xg_for REAL NOT NULL CHECK(xg_for >= 0 AND xg_for <= 15),
        xg_against REAL NOT NULL CHECK(xg_against >= 0 AND xg_against <= 15),
        source_url TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        source_status TEXT NOT NULL,
        PRIMARY KEY(match_id, team_side, source_url)
      )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_xg_observations_match_side ON xg_observations(match_id,team_side)")
    counts = {"read": 0, "inserted_or_updated": 0, "rejected": 0}
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        required = {"match_id","team_side","xg_for","xg_against","source_url","observed_at","source_status"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise SystemExit("XG_CSV_INVALID_COLUMNS: required=" + ",".join(sorted(required)))
        for row in reader:
            counts["read"] += 1
            try:
                mid = row["match_id"].strip()
                side = row["team_side"].strip().lower()
                url = row["source_url"].strip()
                status = row["source_status"].strip().lower()
                if not mid or side not in ("home","away") or not url.startswith(("https://","http://")):
                    raise ValueError("missing match_id, invalid team_side, or invalid source_url")
                if status not in ALLOWED_STATUS:
                    raise ValueError("source_status must be source-verified")
                xgf, xga = float(row["xg_for"]), float(row["xg_against"])
                if not (0 <= xgf <= 15 and 0 <= xga <= 15):
                    raise ValueError("xG outside valid bounds")
                observed = parse_dt(row["observed_at"].strip(), "observed_at")
                if not con.execute("SELECT 1 FROM matches WHERE match_id=?", (mid,)).fetchone():
                    raise ValueError("match_id does not exist in matches table")
                con.execute("""
                  INSERT INTO xg_observations
                    (match_id,team_side,xg_for,xg_against,source_url,observed_at,source_status)
                  VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT(match_id,team_side,source_url) DO UPDATE SET
                    xg_for=excluded.xg_for,xg_against=excluded.xg_against,
                    observed_at=excluded.observed_at,source_status=excluded.source_status
                """, (mid,side,xgf,xga,url,observed,status))
                counts["inserted_or_updated"] += 1
            except Exception as exc:
                counts["rejected"] += 1
                print(f"REJECT row={counts['read']} reason={exc}", file=sys.stderr)
    con.commit()
    counts["usable_verified_rows"] = con.execute("""
      SELECT COUNT(*) FROM xg_observations
      WHERE lower(trim(source_status)) IN ('verified','confirmed','valid','success','ok','available')
    """).fetchone()[0]
    print(counts)
    con.close()
    if counts["rejected"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
