#!/usr/bin/env python3
"""Isolated tests for append-only xG records and reproducible ISO-week summaries."""
import sqlite3
import tempfile
from pathlib import Path
from xg_snapshot_store import init_db, append_match_xg, append_weekly_summary, audit_weekly_coverage

def record(**overrides):
    r = {
      "match_id":"test-match-1","provider_event_id":"provider-1","team_id":"team-home",
      "competition_id":"league-test","season_id":"season-test","match_date":"2026-10-12",
      "home_away":"home","provider":"TEST_PROVIDER","provider_version":"v1",
      "source_url":"https://example.invalid/test","captured_at_utc":"2026-10-12T15:00:00+00:00",
      "value_type":"MEASURED","xg_for":1.7,"xg_against":0.8,"missing_reason":None,
      "raw_payload_sha256":"a"*64
    }
    r.update(overrides)
    return r

def main():
    with tempfile.TemporaryDirectory() as td:
        con = init_db(Path(td)/"xg.sqlite")
        _, digest = append_match_xg(con, record())
        assert len(digest) == 64
        append_match_xg(con, record(
          xg_record_id="test-missing-record",match_id="test-match-2",
          provider_event_id="provider-2",match_date="2026-10-15",
          captured_at_utc="2026-10-15T15:00:00+00:00",value_type="MISSING",
          xg_for=None,xg_against=None,missing_reason="provider did not publish match xG"
        ))
        summary = append_weekly_summary(con,"team-home","league-test","season-test",2026,42,
                                        "TEST_PROVIDER","v1","MEASURED")
        assert summary["matches_count"] == 1
        assert summary["xg_covered_matches"] == 1
        assert abs(summary["week_xg"]-1.7) < 1e-9
        assert abs(summary["week_xga"]-0.8) < 1e-9
        assert abs(summary["week_xgd"]-0.9) < 1e-9
        assert abs(summary["avg_xgd_per90"]-0.9) < 1e-9
        missing_summary = append_weekly_summary(con,"team-home","league-test","season-test",2026,42,
                                                 "TEST_PROVIDER","v1","MISSING")
        assert missing_summary["matches_count"] == 1
        assert missing_summary["coverage"] == 0
        coverage = audit_weekly_coverage(con)
        assert len(coverage) == 2
        assert all(row["weekly_snapshot_exists"] for row in coverage)
        for sql in (
          "UPDATE match_xg_observation SET xg_for=99 WHERE xg_record_id=(SELECT MIN(xg_record_id) FROM match_xg_observation)",
          "DELETE FROM match_xg_observation"
        ):
          try:
            con.execute(sql); con.commit()
          except sqlite3.IntegrityError:
            con.rollback()
          else:
            raise AssertionError("xG history mutation unexpectedly succeeded")
        try:
          con.execute("UPDATE team_weekly_xg_snapshot SET week_xg=99")
          con.commit()
        except sqlite3.IntegrityError:
          con.rollback()
        else:
          raise AssertionError("weekly snapshot mutation unexpectedly succeeded")
        con.close()
    print("XG_SNAPSHOT_STORE_TEST=PASS; measured/missing separation, weekly recomputation, append-only triggers verified")

if __name__ == "__main__":
    main()
