#!/usr/bin/env python3
"""Smoke tests for the versioned prediction lineage schema; temporary DB only."""
import sqlite3
import tempfile
from pathlib import Path
from prediction_trace_v2 import init_db, append_fixture_provider_id, append_prediction, append_market

def main():
    with tempfile.TemporaryDirectory() as td:
        con = init_db(Path(td) / "lineage.sqlite")
        append_fixture_provider_id(con, {
          "mapping_id":"map-1","match_id":"match-1","provider":"LEISU",
          "provider_event_id":"ls-123","identity_status":"VERIFIED",
          "evidence_url":"https://example.invalid/fixture",
          "captured_at_utc":"2026-10-15T20:00:00+00:00"
        })
        record = {
          "prediction_id":"pred-1","match_id":"match-1",
          "provider_event_ids":{"LEISU":"ls-123","SPORTTERY":"sp-456"},
          "prediction_timestamp_utc":"2026-10-16T00:00:00+00:00",
          "prediction_timestamp_beijing":"2026-10-16T08:00:00+08:00",
          "kickoff_utc":"2026-10-16T12:00:00+00:00","lead_minutes":720,
          "prediction_window_status":"T_MINUS_12H","model_name":"V5.1",
          "model_version":"V5.1","git_commit_sha":"a"*40,"config_sha256":"b"*64,
          "feature_snapshot_id":"feature-1","feature_cutoff_utc":"2026-10-15T23:45:00+00:00",
          "home_win_prob":0.45,"draw_prob":0.27,"away_win_prob":0.28,
          "lambda_home":1.42,"lambda_away":1.03,"score1_home":1,"score1_away":0,
          "score2_home":2,"score2_away":1,"predicted_90min_result":"H",
          "predicted_ht_ft":"D/H","confidence":0.61,"sporttery_handicap_line":-1,
          "predicted_handicap_result":"D","risk_flags":["lineup_unconfirmed"]
        }
        digest = append_prediction(con, record)
        assert len(digest) == 64
        assert con.execute("SELECT predicted_90min_result,model_version FROM prediction_snapshot_v2").fetchone() == ("H","V5.1")
        late = dict(record, prediction_id="late", prediction_timestamp_utc="2026-10-16T13:00:00+00:00")
        try:
            append_prediction(con, late)
        except ValueError as exc:
            assert "before kickoff" in str(exc)
        else:
            raise AssertionError("post-kickoff prediction was accepted")
        raw = {"event_id":"ls-123","market":{"home":2.0,"draw":3.1,"away":3.5}}
        mdigest = append_market(con, {
          "market_snapshot_id":"market-1","match_id":"match-1","prediction_id":"pred-1",
          "provider":"LEISU","provider_event_id":"ls-123","market_type":"1X2",
          "source_url":"https://example.invalid/market","provider_event_timestamp_utc":None,
          "retrieved_at_utc":"2026-10-16T00:05:00+00:00","timestamp_quality":"RETRIEVAL_TIME_ONLY",
          "raw_payload":raw,"normalized":{"home":2.0,"draw":3.1,"away":3.5},
          "quality_status":"VALID_PREMATCH"
        })
        assert len(mdigest) == 64
        for table in ("fixture_provider_map","prediction_snapshot_v2","market_snapshot_v2"):
            try:
                con.execute("DELETE FROM " + table)
                con.commit()
            except sqlite3.IntegrityError:
                con.rollback()
            else:
                raise AssertionError(table + " allowed delete")
        con.close()
    print("PREDICTION_TRACE_V2_TEST=PASS; IDs, probability/lambda, time cutoff, scores, HT/FT, confidence, hashes and append-only rules verified")

if __name__ == "__main__":
    main()
