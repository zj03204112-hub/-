"""Append-only writer for prediction-time records. This module does not run or alter a model."""
import hashlib, json, os, sqlite3, math
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prediction_trace (
  prediction_id TEXT PRIMARY KEY,
  match_id TEXT NOT NULL,
  prediction_timestamp_utc TEXT NOT NULL,
  prediction_timestamp_beijing TEXT NOT NULL,
  kickoff_utc TEXT NOT NULL,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  git_commit_sha TEXT NOT NULL,
  config_sha256 TEXT NOT NULL,
  feature_snapshot_id TEXT,
  home_win_prob REAL NOT NULL,
  draw_prob REAL NOT NULL,
  away_win_prob REAL NOT NULL,
  lambda_home REAL NOT NULL,
  lambda_away REAL NOT NULL,
  score1_home INTEGER NOT NULL,
  score1_away INTEGER NOT NULL,
  score2_home INTEGER,
  score2_away INTEGER,
  sporttery_handicap_line REAL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS prediction_trace_no_update
BEFORE UPDATE ON prediction_trace BEGIN
  SELECT RAISE(ABORT, 'prediction_trace is append-only; create a correction record instead');
END;
CREATE TRIGGER IF NOT EXISTS prediction_trace_no_delete
BEFORE DELETE ON prediction_trace BEGIN
  SELECT RAISE(ABORT, 'prediction_trace is append-only; delete forbidden');
END;
CREATE TABLE IF NOT EXISTS market_trace (
  market_snapshot_id TEXT PRIMARY KEY,
  match_id TEXT NOT NULL,
  prediction_id TEXT,
  provider TEXT NOT NULL,
  market_type TEXT NOT NULL,
  source_url TEXT,
  provider_event_timestamp_utc TEXT,
  retrieved_at_utc TEXT NOT NULL,
  retrieved_at_beijing TEXT NOT NULL,
  timestamp_quality TEXT NOT NULL,
  normalized_json TEXT NOT NULL,
  raw_payload_json TEXT NOT NULL,
  raw_payload_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS market_trace_no_update
BEFORE UPDATE ON market_trace BEGIN
  SELECT RAISE(ABORT, 'market_trace is append-only; correction must be a new record');
END;
CREATE TRIGGER IF NOT EXISTS market_trace_no_delete
BEFORE DELETE ON market_trace BEGIN
  SELECT RAISE(ABORT, 'market_trace is append-only; delete forbidden');
END;
"""

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.commit()
    return con

def append_prediction(con, record):
    """record must contain all required keys; payload is stored and hashed as received."""
    required = [
      "prediction_id","match_id","prediction_timestamp_utc","prediction_timestamp_beijing",
      "kickoff_utc","model_name","model_version","git_commit_sha","config_sha256",
      "home_win_prob","draw_prob","away_win_prob","lambda_home","lambda_away",
      "score1_home","score1_away"
    ]
    missing = [k for k in required if record.get(k) is None or record.get(k) == ""]
    if missing:
        raise ValueError("missing required prediction fields: " + ", ".join(missing))
    probs = [float(record[k]) for k in ("home_win_prob","draw_prob","away_win_prob")]
    if not all(math.isfinite(p) and 0 <= p <= 1 for p in probs) or abs(sum(probs)-1.0) > 0.01:
        raise ValueError("1X2 probabilities must be finite, in [0,1], and sum to 1±0.01")
    lambdas = [float(record["lambda_home"]), float(record["lambda_away"])]
    if not all(math.isfinite(v) and v >= 0 for v in lambdas):
        raise ValueError("lambda values must be finite and non-negative")
    try:
        prediction_time = datetime.fromisoformat(record["prediction_timestamp_utc"].replace("Z", "+00:00"))
        kickoff_time = datetime.fromisoformat(record["kickoff_utc"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("prediction_timestamp_utc and kickoff_utc must be ISO-8601 timestamps") from exc
    if prediction_time.tzinfo is None or kickoff_time.tzinfo is None:
        raise ValueError("prediction timestamp and kickoff must include timezone offsets")
    if prediction_time >= kickoff_time:
        raise ValueError("pre-match prediction timestamp must be strictly before kickoff")
    if len(str(record["config_sha256"])) != 64 or any(c not in "0123456789abcdefABCDEF" for c in str(record["config_sha256"])):
        raise ValueError("config_sha256 must be a 64-character SHA-256 hex digest")
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",",":"))
    digest = sha256_text(payload)
    con.execute("""
      INSERT INTO prediction_trace (
        prediction_id,match_id,prediction_timestamp_utc,prediction_timestamp_beijing,
        kickoff_utc,model_name,model_version,git_commit_sha,config_sha256,feature_snapshot_id,
        home_win_prob,draw_prob,away_win_prob,lambda_home,lambda_away,
        score1_home,score1_away,score2_home,score2_away,sporttery_handicap_line,
        payload_json,payload_sha256
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
      record["prediction_id"],record["match_id"],record["prediction_timestamp_utc"],
      record["prediction_timestamp_beijing"],record["kickoff_utc"],record["model_name"],
      record["model_version"],record["git_commit_sha"],record["config_sha256"],
      record.get("feature_snapshot_id"),*probs,float(record["lambda_home"]),
      float(record["lambda_away"]),int(record["score1_home"]),int(record["score1_away"]),
      record.get("score2_home"),record.get("score2_away"),record.get("sporttery_handicap_line"),
      payload,digest
    ))
    con.commit()
    return digest

def append_market(con, record):
    required = ["market_snapshot_id","match_id","provider","market_type","retrieved_at_utc",
                "retrieved_at_beijing","timestamp_quality","raw_payload"]
    missing = [k for k in required if record.get(k) is None or record.get(k) == ""]
    if missing:
        raise ValueError("missing required market fields: " + ", ".join(missing))
    raw = json.dumps(record["raw_payload"], ensure_ascii=False, sort_keys=True, separators=(",",":"))
    normalized = json.dumps(record.get("normalized",{}), ensure_ascii=False, sort_keys=True, separators=(",",":"))
    digest = sha256_text(raw)
    con.execute("""
      INSERT INTO market_trace (
        market_snapshot_id,match_id,prediction_id,provider,market_type,source_url,
        provider_event_timestamp_utc,retrieved_at_utc,retrieved_at_beijing,timestamp_quality,
        normalized_json,raw_payload_json,raw_payload_sha256
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (record["market_snapshot_id"],record["match_id"],record.get("prediction_id"),
          record["provider"],record["market_type"],record.get("source_url"),
          record.get("provider_event_timestamp_utc"),record["retrieved_at_utc"],
          record["retrieved_at_beijing"],record["timestamp_quality"],normalized,raw,digest))
    con.commit()
    return digest
