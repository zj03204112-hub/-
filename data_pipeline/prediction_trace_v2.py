"""Versioned append-only prediction lineage store; independent of model logic."""
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixture_provider_map (
 mapping_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, provider TEXT NOT NULL,
 provider_event_id TEXT NOT NULL, identity_status TEXT NOT NULL
 CHECK(identity_status IN ('VERIFIED','AMBIGUOUS','UNRESOLVED')),
 evidence_url TEXT, captured_at_utc TEXT NOT NULL, raw_payload_sha256 TEXT,
 payload_sha256 TEXT NOT NULL, UNIQUE(provider,provider_event_id,match_id)
);
CREATE TABLE IF NOT EXISTS prediction_snapshot_v2 (
 prediction_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, provider_event_ids_json TEXT NOT NULL,
 prediction_timestamp_utc TEXT NOT NULL, prediction_timestamp_beijing TEXT NOT NULL,
 kickoff_utc TEXT NOT NULL, lead_minutes INTEGER NOT NULL, prediction_window_status TEXT NOT NULL,
 model_name TEXT NOT NULL, model_version TEXT NOT NULL, git_commit_sha TEXT NOT NULL,
 config_sha256 TEXT NOT NULL, feature_snapshot_id TEXT, feature_cutoff_utc TEXT NOT NULL,
 home_win_prob REAL NOT NULL, draw_prob REAL NOT NULL, away_win_prob REAL NOT NULL,
 lambda_home REAL NOT NULL, lambda_away REAL NOT NULL,
 score1_home INTEGER NOT NULL, score1_away INTEGER NOT NULL,
 score2_home INTEGER, score2_away INTEGER,
 predicted_90min_result TEXT NOT NULL CHECK(predicted_90min_result IN ('H','D','A')),
 predicted_ht_ft TEXT NOT NULL, confidence REAL NOT NULL,
 sporttery_handicap_line REAL, predicted_handicap_result TEXT,
 risk_flags_json TEXT NOT NULL, raw_prediction_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_snapshot_v2 (
 market_snapshot_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, prediction_id TEXT,
 provider TEXT NOT NULL, provider_event_id TEXT, market_type TEXT NOT NULL, source_url TEXT,
 provider_event_timestamp_utc TEXT, retrieved_at_utc TEXT NOT NULL,
 timestamp_quality TEXT NOT NULL CHECK(timestamp_quality IN
 ('PROVIDER_TIMESTAMPED','RETRIEVAL_TIME_ONLY','UNTIMESTAMPED')),
 raw_payload_json TEXT NOT NULL, raw_payload_sha256 TEXT NOT NULL,
 normalized_json TEXT NOT NULL, quality_status TEXT NOT NULL
);
"""

def sha256_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def init_db(path):
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    for table in ("fixture_provider_map", "prediction_snapshot_v2", "market_snapshot_v2"):
        con.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS {table}_no_update
        BEFORE UPDATE ON {table} BEGIN
          SELECT RAISE(ABORT, '{table} is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS {table}_no_delete
        BEFORE DELETE ON {table} BEGIN
          SELECT RAISE(ABORT, '{table} is append-only');
        END;
        """)
    con.commit()
    return con

def _aware(value, field):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(field + " must be ISO-8601") from exc
    if dt.tzinfo is None:
        raise ValueError(field + " must include timezone")
    return dt

def append_fixture_provider_id(con, record):
    required = ("mapping_id", "match_id", "provider", "provider_event_id",
                "identity_status", "captured_at_utc")
    missing = [k for k in required if record.get(k) in (None, "")]
    if missing:
        raise ValueError("missing fixture mapping fields: " + ", ".join(missing))
    if record["identity_status"] not in ("VERIFIED", "AMBIGUOUS", "UNRESOLVED"):
        raise ValueError("invalid identity_status")
    _aware(record["captured_at_utc"], "captured_at_utc")
    con.execute("""INSERT INTO fixture_provider_map
      (mapping_id,match_id,provider,provider_event_id,identity_status,evidence_url,
       captured_at_utc,raw_payload_sha256,payload_sha256) VALUES (?,?,?,?,?,?,?,?,?)""",
      (record["mapping_id"], record["match_id"], record["provider"], record["provider_event_id"],
       record["identity_status"], record.get("evidence_url"), record["captured_at_utc"],
       record.get("raw_payload_sha256"), sha256_json(record)))
    con.commit()

def append_prediction(con, record):
    required = (
      "prediction_id", "match_id", "provider_event_ids", "prediction_timestamp_utc",
      "prediction_timestamp_beijing", "kickoff_utc", "lead_minutes", "prediction_window_status",
      "model_name", "model_version", "git_commit_sha", "config_sha256", "feature_cutoff_utc",
      "home_win_prob", "draw_prob", "away_win_prob", "lambda_home", "lambda_away",
      "score1_home", "score1_away", "predicted_90min_result", "predicted_ht_ft", "confidence"
    )
    missing = [k for k in required if record.get(k) is None or record.get(k) == ""]
    if missing:
        raise ValueError("missing prediction fields: " + ", ".join(missing))
    probs = [float(record[k]) for k in ("home_win_prob", "draw_prob", "away_win_prob")]
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in probs) or abs(sum(probs)-1) > 0.01:
        raise ValueError("1X2 probabilities must be valid and sum to 1±0.01")
    lambdas = [float(record["lambda_home"]), float(record["lambda_away"])]
    if not all(math.isfinite(v) and v >= 0 for v in lambdas):
        raise ValueError("lambda values must be finite and non-negative")
    confidence = float(record["confidence"])
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be in [0,1]")
    pt = _aware(record["prediction_timestamp_utc"], "prediction_timestamp_utc")
    kt = _aware(record["kickoff_utc"], "kickoff_utc")
    cutoff = _aware(record["feature_cutoff_utc"], "feature_cutoff_utc")
    if pt >= kt:
        raise ValueError("prediction must be before kickoff")
    if cutoff > pt:
        raise ValueError("feature cutoff cannot be after prediction time")
    if record["predicted_90min_result"] not in ("H", "D", "A"):
        raise ValueError("predicted_90min_result must be H, D, or A")
    if not isinstance(record["provider_event_ids"], dict) or not record["provider_event_ids"]:
        raise ValueError("provider_event_ids must be a non-empty provider ID/status mapping")
    config_hash = str(record["config_sha256"])
    if len(config_hash) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in config_hash):
        raise ValueError("config_sha256 must be a SHA-256 hex digest")
    payload = dict(record)
    provider_ids_json = json.dumps(record["provider_event_ids"], ensure_ascii=False, sort_keys=True)
    risk_json = json.dumps(record.get("risk_flags", []), ensure_ascii=False, sort_keys=True)
    raw_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    con.execute("""INSERT INTO prediction_snapshot_v2
      (prediction_id,match_id,provider_event_ids_json,prediction_timestamp_utc,
       prediction_timestamp_beijing,kickoff_utc,lead_minutes,prediction_window_status,
       model_name,model_version,git_commit_sha,config_sha256,feature_snapshot_id,
       feature_cutoff_utc,home_win_prob,draw_prob,away_win_prob,lambda_home,lambda_away,
       score1_home,score1_away,score2_home,score2_away,predicted_90min_result,predicted_ht_ft,
       confidence,sporttery_handicap_line,predicted_handicap_result,risk_flags_json,
       raw_prediction_json,payload_sha256)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (record["prediction_id"], record["match_id"], provider_ids_json,
       record["prediction_timestamp_utc"], record["prediction_timestamp_beijing"],
       record["kickoff_utc"], int(record["lead_minutes"]), record["prediction_window_status"],
       record["model_name"], record["model_version"], record["git_commit_sha"], config_hash,
       record.get("feature_snapshot_id"), record["feature_cutoff_utc"], *probs,
       *lambdas, int(record["score1_home"]), int(record["score1_away"]),
       record.get("score2_home"), record.get("score2_away"), record["predicted_90min_result"],
       record["predicted_ht_ft"], confidence, record.get("sporttery_handicap_line"),
       record.get("predicted_handicap_result"), risk_json, raw_json, digest))
    con.commit()
    return digest

def append_market(con, record):
    required = ("market_snapshot_id", "match_id", "provider", "market_type",
                "retrieved_at_utc", "timestamp_quality", "raw_payload")
    missing = [k for k in required if record.get(k) in (None, "")]
    if missing:
        raise ValueError("missing market fields: " + ", ".join(missing))
    _aware(record["retrieved_at_utc"], "retrieved_at_utc")
    if record["timestamp_quality"] not in ("PROVIDER_TIMESTAMPED", "RETRIEVAL_TIME_ONLY", "UNTIMESTAMPED"):
        raise ValueError("invalid timestamp_quality")
    raw = json.dumps(record["raw_payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    normalized = json.dumps(record.get("normalized", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    con.execute("""INSERT INTO market_snapshot_v2
      (market_snapshot_id,match_id,prediction_id,provider,provider_event_id,market_type,source_url,
       provider_event_timestamp_utc,retrieved_at_utc,timestamp_quality,raw_payload_json,
       raw_payload_sha256,normalized_json,quality_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (record["market_snapshot_id"], record["match_id"], record.get("prediction_id"),
       record["provider"], record.get("provider_event_id"), record["market_type"],
       record.get("source_url"), record.get("provider_event_timestamp_utc"),
       record["retrieved_at_utc"], record["timestamp_quality"], raw, digest, normalized,
       record.get("quality_status", "MISSING_FIELDS")))
    con.commit()
    return digest
