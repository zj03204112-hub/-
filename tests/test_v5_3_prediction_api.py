import sqlite3
import tempfile
import unittest
from pathlib import Path

from data_pipeline.v5_3_prediction_api import predict_and_archive


class V53PredictionApiTests(unittest.TestCase):
    def test_predict_and_archive_writes_prediction_and_market(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "trace.sqlite")
            out = predict_and_archive(
                db_path=db,
                match_id="fixture-verified-001",
                prediction_id="prediction-test-001",
                git_commit_sha="a" * 40,
                home_team="Home FC", away_team="Away FC",
                kickoff_iso="2026-11-20T19:00:00+08:00",
                snapshot_time_iso="2026-11-20T08:00:00+08:00",
                odds_provider="verified-test-provider",
                odds_source_url="https://example.invalid/fixture",
                odds_verified=True,
                goal_line=2.5, over_odds=1.90, under_odds=1.90,
                home_odds=2.10, draw_odds=3.30, away_odds=3.60,
                max_goals=10,
            )
            self.assertTrue(out["archive"]["prediction_and_market_trace_appended"])
            self.assertAlmostEqual(out["probability_mass"], 1.0, places=10)
            con = sqlite3.connect(db)
            try:
                prediction_count = con.execute(
                    "SELECT COUNT(*) FROM prediction_trace WHERE prediction_id=?",
                    ("prediction-test-001",),
                ).fetchone()[0]
                market_count = con.execute(
                    "SELECT COUNT(*) FROM market_trace WHERE prediction_id=?",
                    ("prediction-test-001",),
                ).fetchone()[0]
                self.assertEqual(prediction_count, 1)
                self.assertEqual(market_count, 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(
                        "UPDATE prediction_trace SET draw_prob=0 WHERE prediction_id=?",
                        ("prediction-test-001",),
                    )
            finally:
                con.close()

    def test_rejects_missing_verified_odds(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                predict_and_archive(
                    db_path=str(Path(temp) / "trace.sqlite"),
                    match_id="fixture-verified-002",
                    prediction_id="prediction-test-002",
                    git_commit_sha="b" * 40,
                    home_team="Home FC", away_team="Away FC",
                    kickoff_iso="2026-11-20T19:00:00+08:00",
                    snapshot_time_iso="2026-11-20T08:00:00+08:00",
                    odds_provider="provider",
                    odds_source_url="https://example.invalid/fixture",
                    odds_verified=False,
                    goal_line=2.5, over_odds=1.90, under_odds=1.90,
                    home_odds=2.10, draw_odds=3.30, away_odds=3.60,
                )


if __name__ == "__main__":
    unittest.main()
