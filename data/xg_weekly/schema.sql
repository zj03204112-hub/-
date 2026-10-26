-- Append-only team xG trace schema; proposal only until applied to a connected SQLite DB.
-- ISO week is Monday-Sunday. Source/provider/version are part of the identity of a measurement.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS team_xg_match (
  xg_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id TEXT,
  competition_id INTEGER NOT NULL,
  season_id INTEGER NOT NULL,
  team_id TEXT,
  team_name TEXT NOT NULL,
  team_side TEXT NOT NULL CHECK(team_side IN ('HOME','AWAY')),
  match_date TEXT NOT NULL,
  iso_week TEXT NOT NULL,
  xg REAL,
  xga REAL,
  xgd REAL,
  provider TEXT,
  provider_version TEXT,
  value_type TEXT NOT NULL CHECK(value_type IN ('MEASURED_PROVIDER','MODEL_ESTIMATE','MISSING')),
  provider_event_timestamp TEXT,
  retrieved_at TEXT NOT NULL,
  raw_payload_ref TEXT,
  raw_payload_sha256 TEXT,
  identity_status TEXT NOT NULL CHECK(identity_status IN ('VERIFIED','AMBIGUOUS','UNRESOLVED')),
  missing_status TEXT,
  missing_reason TEXT,
  created_at TEXT NOT NULL,
  CHECK (
    (value_type='MISSING' AND xg IS NULL AND xga IS NULL AND xgd IS NULL AND missing_reason IS NOT NULL)
    OR
    (value_type IN ('MEASURED_PROVIDER','MODEL_ESTIMATE') AND xg IS NOT NULL AND xga IS NOT NULL AND xgd IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_team_xg_week
  ON team_xg_match(team_id, competition_id, season_id, iso_week);
CREATE INDEX IF NOT EXISTS idx_team_xg_match
  ON team_xg_match(match_id, team_side);

CREATE TRIGGER IF NOT EXISTS team_xg_match_no_update
BEFORE UPDATE ON team_xg_match
BEGIN SELECT RAISE(ABORT, 'team_xg_match is append-only; insert a correction/version row'); END;

CREATE TRIGGER IF NOT EXISTS team_xg_match_no_delete
BEFORE DELETE ON team_xg_match
BEGIN SELECT RAISE(ABORT, 'team_xg_match is append-only'); END;

CREATE TABLE IF NOT EXISTS team_xg_weekly_summary (
  summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id TEXT,
  team_name TEXT NOT NULL,
  competition_id INTEGER NOT NULL,
  season_id INTEGER NOT NULL,
  iso_week TEXT NOT NULL,
  provider TEXT,
  provider_version TEXT,
  week_xg REAL,
  week_xga REAL,
  week_xgd REAL,
  matches_count INTEGER,
  xg_per90 REAL,
  xga_per90 REAL,
  xgd_per90 REAL,
  coverage_status TEXT NOT NULL CHECK(coverage_status IN ('COMPLETE','PARTIAL','MISSING')),
  missing_reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS team_xg_weekly_summary_no_update
BEFORE UPDATE ON team_xg_weekly_summary
BEGIN SELECT RAISE(ABORT, 'weekly xG snapshots are immutable; append a new snapshot'); END;

CREATE TRIGGER IF NOT EXISTS team_xg_weekly_summary_no_delete
BEFORE DELETE ON team_xg_weekly_summary
BEGIN SELECT RAISE(ABORT, 'weekly xG snapshots are immutable'); END;
