import unittest

from model_validation.v5_3_direct_matrix_repair import build_score_matrix
from model_validation.v5_3_prediction_engine import predict_match


class DirectMatrixRepairTests(unittest.TestCase):
    def test_matrix_is_normalized_and_outputs_are_consistent(self):
        out = build_score_matrix(
            lambda_home=1.65, lambda_away=1.20,
            market_home_probability=0.48,
            market_draw_probability=0.27,
            market_away_probability=0.25,
            min_goals=12,
        )
        self.assertAlmostEqual(out["probability_mass"], 1.0, places=10)
        self.assertAlmostEqual(out["p_home"] + out["p_draw"] + out["p_away"], 1.0, places=10)
        self.assertAlmostEqual(sum(out["total_goal_bins"].values()), 1.0, places=10)
        self.assertAlmostEqual(sum(out["absolute_net_goal_bins"].values()), 1.0, places=10)
        self.assertAlmostEqual(sum(out["away_goal_bins"].values()), 1.0, places=10)
        self.assertEqual(len(out["top_scores"]), 2)
        self.assertGreater(out["draw_exact_probabilities"]["1-1"], 0)
        self.assertGreater(out["total_goal_bins"]["8+"], 0)
        self.assertGreaterEqual(out["max_goals_per_team"], 12)

    def test_engine_requires_verified_pre_match_odds_and_uses_one_matrix(self):
        args = dict(
            home_team="Home FC", away_team="Away FC",
            kickoff_iso="2026-11-20T19:00:00+08:00",
            snapshot_time_iso="2026-11-20T08:00:00+08:00",
            odds_source="verified-test-fixture", odds_verified=True,
            goal_line=2.5, over_odds=1.90, under_odds=1.90,
            home_odds=2.10, draw_odds=3.30, away_odds=3.60,
            max_goals=10,
        )
        out = predict_match(**args)
        self.assertAlmostEqual(out["probability_mass"], 1.0, places=10)
        self.assertAlmostEqual(out["p_home"] + out["p_draw"] + out["p_away"], 1.0, places=10)
        self.assertEqual(out["model_version"], "V5.3-direct-matrix-repair-v1")
        self.assertFalse(out["production_v5_1_changed"])
        self.assertEqual(len(out["top_scores"]), 2)

    def test_engine_rejects_unverified_odds(self):
        args = dict(
            home_team="Home FC", away_team="Away FC",
            kickoff_iso="2026-11-20T19:00:00+08:00",
            snapshot_time_iso="2026-11-20T08:00:00+08:00",
            odds_source="test", odds_verified=False,
            goal_line=2.5, over_odds=1.9, under_odds=1.9,
            home_odds=2.1, draw_odds=3.3, away_odds=3.6,
        )
        with self.assertRaises(ValueError):
            predict_match(**args)


if __name__ == "__main__":
    unittest.main()
