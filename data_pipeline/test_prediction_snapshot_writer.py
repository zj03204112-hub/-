#!/usr/bin/env python3
"""Isolated smoke test for append-only trace storage; never touches production DB."""
import hashlib
import sqlite3
import tempfile
from pathlib import Path
from prediction_snapshot_writer import init_db, append_prediction, append_market

def main():
    with tempfile.TemporaryDirectory() as td:
        con = init_db(Path(td) / "trace.sqlite")
        record = {
            "prediction_id":"smoke-pred-001","match_id":"smoke-match-001",
            "prediction_timestamp_utc":"2026-10-16T00:00:00+00:00",
            "prediction_timestamp_beijing":"2026-10-16T08:00:00+08:00",
            "kickoff_utc":"2026-10-16T12:00:00+00:00",
            "model_name":"TRACEABILITY_SMOKE_ONLY","model_version":"smoke-0",
            "git_commit_sha":"smoke-commit","config_sha256":"a"*64,
            "home_win_prob":0.45,"draw_prob":0.27,"away_win_prob":0.28,
            "lambda_home":1.42,"lambda_away":1.03,
            "score1_home":1,"score1_away":0,"score2_home":2,"score2_away":1,
            "sporttery_handicap_line":-1
        }
        late = dict(record)
        late["prediction_id"] = "smoke-pred-after-kickoff"
        late["prediction_timestamp_utc"] = "2026-10-16T13:00:00+00:00"
        try:
            append_prediction(con, late)
        except ValueError as exc:
            assert "strictly before kickoff" in str(exc)
        else:
            raise AssertionError("post-kickoff prediction was accepted as a pre-match snapshot")
        digest = append_prediction(con, record)
        assert len(digest) == 64
        stored = con.execute("SELECT COUNT(*) FROM prediction_trace").fetchone()[0]
        assert stored == 1
        for sql in (
            "UPDATE prediction_trace SET model_version='changed' WHERE prediction_id='smoke-pred-001'",
            "DELETE FROM prediction_trace WHERE prediction_id='smoke-pred-001'"
        ):
            try:
                con.execute(sql)
                con.commit()
            except sqlite3.IntegrityError:
                con.rollback()
            else:
                raise AssertionError("prediction trace mutation unexpectedly succeeded")
        raw = {"fixture":"smoke-match-001","event_time":None,"market":{"h":2.1,"d":3.2,"a":3.4}}
        mdigest = append_market(con, {
            "market_snapshot_id":"smoke-market-001","match_id":"smoke-match-001",
            "prediction_id":"smoke-pred-001","provider":"SMOKE_TEST","market_type":"1X2",
            "source_url":"https://example.invalid/smoke",
            "provider_event_timestamp_utc":None,
            "retrieved_at_utc":"2026-10-16T00:01:00+00:00",
            "retrieved_at_beijing":"2026-10-16T08:01:00+08:00",
            "timestamp_quality":"RETRIEVAL_TIME_ONLY","normalized":{"home":2.1,"draw":3.2,"away":3.4},
            "raw_payload":raw
        })
        assert mdigest == hashlib.sha256(
            __import__("json").dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",",":")).encode("utf-8")
        ).hexdigest()
        assert con.execute("SELECT timestamp_quality FROM market_trace").fetchone()[0] == "RETRIEVAL_TIME_ONLY"
        assert con.execute("SELECT COUNT(*) FROM market_trace").fetchone()[0] == 1
        con.close()
    print("TRACEABILITY_SMOKE_TEST=PASS; prediction append-only, market raw hash, timestamp-quality distinction verified")

if __name__ == "__main__":
    main()
