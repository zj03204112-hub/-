-- Own football database layer v1.0
-- Additive only: creates own_* tables; never drops, renames, or alters existing tables.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS own_competitions (
  competition_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  country TEXT,
  provider_code TEXT,
  competition_type TEXT NOT NULL DEFAULT 'league',
  created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE(canonical_name, competition_type)
);

CREATE TABLE IF NOT EXISTS own_teams (
  team_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  country TEXT,
  created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS own_team_aliases (
  alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id TEXT NOT NULL REFERENCES own_teams(team_id),
  provider TEXT NOT NULL,
  alias_name TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (verification_status IN ('pending','verified','rejected')),
  source_ref TEXT,
  UNIQUE(provider, normalized_alias)
);

CREATE TABLE IF NOT EXISTS own_matches (
  match_id TEXT PRIMARY KEY,
  competition_id TEXT NOT NULL REFERENCES own_competitions(competition_id),
  season TEXT,
  kickoff_utc TEXT NOT NULL,
  home_team_id TEXT NOT NULL REFERENCES own_teams(team_id),
  away_team_id TEXT NOT NULL REFERENCES own_teams(team_id),
  status TEXT NOT NULL DEFAULT 'scheduled'
    CHECK (status IN ('scheduled','live','finished','postponed','cancelled','unknown')),
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  source_url TEXT,
  created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE(provider, provider_event_id),
  CHECK (home_team_id <> away_team_id)
);
CREATE INDEX IF NOT EXISTS idx_own_matches_kickoff ON own_matches(kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_own_matches_comp_kickoff ON own_matches(competition_id, kickoff_utc);

CREATE TABLE IF NOT EXISTS own_results (
  match_id TEXT PRIMARY KEY REFERENCES own_matches(match_id),
  home_goals INTEGER NOT NULL CHECK (home_goals >= 0),
  away_goals INTEGER NOT NULL CHECK (away_goals >= 0),
  status TEXT NOT NULL CHECK (status = 'verified'),
  provider TEXT NOT NULL,
  source_url TEXT NOT NULL,
  verified_at_utc TEXT NOT NULL,
  raw_payload_sha256 TEXT
);

CREATE TABLE IF NOT EXISTS own_provider_id_map (
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  match_id TEXT NOT NULL REFERENCES own_matches(match_id),
  verification_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (verification_status IN ('pending','verified','rejected')),
  evidence_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS own_prediction_snapshots (
  prediction_id TEXT PRIMARY KEY,
  match_id TEXT NOT NULL REFERENCES own_matches(match_id),
  prediction_timestamp_utc TEXT NOT NULL,
  kickoff_utc TEXT NOT NULL,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  git_commit_sha TEXT,
  feature_snapshot_json TEXT NOT NULL,
  home_win_prob REAL NOT NULL CHECK(home_win_prob BETWEEN 0 AND 1),
  draw_prob REAL NOT NULL CHECK(draw_prob BETWEEN 0 AND 1),
  away_win_prob REAL NOT NULL CHECK(away_win_prob BETWEEN 0 AND 1),
  lambda_home REAL NOT NULL CHECK(lambda_home >= 0),
  lambda_away REAL NOT NULL CHECK(lambda_away >= 0),
  score1_home INTEGER NOT NULL CHECK(score1_home >= 0),
  score1_away INTEGER NOT NULL CHECK(score1_away >= 0),
  score2_home INTEGER CHECK(score2_home >= 0),
  score2_away INTEGER CHECK(score2_away >= 0),
  sporttery_handicap_line REAL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  CHECK(prediction_timestamp_utc < kickoff_utc),
  CHECK(abs(home_win_prob + draw_prob + away_win_prob - 1.0) <= 0.01)
);
CREATE INDEX IF NOT EXISTS idx_own_predictions_match ON own_prediction_snapshots(match_id, prediction_timestamp_utc);

CREATE TABLE IF NOT EXISTS own_data_audit (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_timestamp_utc TEXT NOT NULL,
  audit_type TEXT NOT NULL,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL,
  artifact_sha256 TEXT
);

-- Prediction and verified-result records are append-only; corrections require new audit/evidence records.
CREATE TRIGGER IF NOT EXISTS own_prediction_no_update
BEFORE UPDATE ON own_prediction_snapshots BEGIN
  SELECT RAISE(ABORT, 'own_prediction_snapshots is append-only');
END;
CREATE TRIGGER IF NOT EXISTS own_prediction_no_delete
BEFORE DELETE ON own_prediction_snapshots BEGIN
  SELECT RAISE(ABORT, 'own_prediction_snapshots is append-only');
END;
CREATE TRIGGER IF NOT EXISTS own_result_no_update
BEFORE UPDATE ON own_results BEGIN
  SELECT RAISE(ABORT, 'own_results is append-only; record a new verification instead');
END;
CREATE TRIGGER IF NOT EXISTS own_result_no_delete
BEFORE DELETE ON own_results BEGIN
  SELECT RAISE(ABORT, 'own_results is append-only');
END;
