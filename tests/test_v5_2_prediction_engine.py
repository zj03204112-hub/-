import unittest
from model_validation.v5_2_prediction_engine import predict_match


class PredictionEngineTests(unittest.TestCase):
    def _args(self):
        return dict(
            home_team="Home FC", away_team="Away FC",
            kickoff_iso="2026-11-01T19:00:00+08:00",
            snapshot_time_iso="2026-11-01T08:00:00+08:00",
            odds_source="verified-test-fixture",
            odds_verified=True,
            goal_line=2.5, over_odds=1.90, under_odds=1.90,
            home_odds=2.10, draw_odds=3.30, away_odds=3.60,
            max_goals=10,
        )

    def test_probability_distribution(self):
        out = predict_match(**self._args())
        self.assertAlmostEqual(out["p_home"] + out["p_draw"] + out["p_away"], 1.0, places=8)
        self.assertAlmostEqual(out["probability_mass"], 1.0, places=8)
        self.assertEqual(len(out["top_scores"]), 2)
        self.assertEqual(out["validation_status"], "CANDIDATE_NOT_BACKTESTED")
        self.assertFalse(out["production_v5_1_changed"])

    def test_rejects_unverified_odds(self):
        args = self._args()
        args["odds_verified"] = False
        with self.assertRaises(ValueError):
            predict_match(**args)

    def test_rejects_snapshot_after_kickoff(self):
        args = self._args()
        args["snapshot_time_iso"] = "2026-11-01T20:00:00+08:00"
        with self.assertRaises(ValueError):
            predict_match(**args)

    def test_rejects_timezone_naive_timestamps(self):
        args = self._args()
        args["snapshot_time_iso"] = "2026-11-01T08:00:00"
        with self.assertRaises(ValueError):
            predict_match(**args)


if __name__ == "__main__":
    unittest.main()
