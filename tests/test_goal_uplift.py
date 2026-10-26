import math
import unittest

from experiments.goal_uplift import score_distribution, shadow_prediction, uplift_lambdas


class GoalUpliftTests(unittest.TestCase):
    def test_base_uplift_below_threshold(self):
        h, a = uplift_lambdas(1.2, 1.3)
        self.assertAlmostEqual(h + a, 2.5 * 1.08)
        self.assertAlmostEqual(h / a, 1.2 / 1.3)

    def test_extra_uplift_uses_original_total_threshold(self):
        h, a = uplift_lambdas(1.4, 1.4)
        self.assertAlmostEqual(h + a, 2.8 * 1.08 * 1.10)

    def test_just_below_threshold_does_not_trigger_extra(self):
        h, a = uplift_lambdas(1.39, 1.40)
        self.assertAlmostEqual(h + a, 2.79 * 1.08)

    def test_invalid_lambdas_rejected(self):
        with self.assertRaises(ValueError):
            uplift_lambdas(-0.1, 1.0)
        with self.assertRaises(ValueError):
            uplift_lambdas(0.0, 0.0)

    def test_score_distribution_probabilities_reasonable(self):
        result = score_distribution(1.5, 1.1, max_goals=15)
        total_1x2 = result["home_win_probability"] + result["draw_probability"] + result["away_win_probability"]
        self.assertTrue(0.999 < total_1x2 <= 1.000001)
        self.assertEqual(len(result["top_scores"]), 2)
        self.assertAlmostEqual(sum(result["total_goals_pmf"]), result["grid_probability_mass"], places=8)

    def test_shadow_does_not_claim_production_change(self):
        result = shadow_prediction(1.4, 1.4)
        self.assertFalse(result["production_v5_1_changed"])
        self.assertEqual(result["mode"], "shadow_only")
        self.assertTrue(math.isclose(result["adjusted_lambda_total"], 3.3264))


if __name__ == "__main__":
    unittest.main()
