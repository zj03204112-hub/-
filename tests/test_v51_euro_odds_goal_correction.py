import unittest

from experiments.v51_euro_odds_goal_correction import (
    correct_goal_lambdas,
    no_vig_1x2,
)


class EuroOddsGoalCorrectionTests(unittest.TestCase):
    def test_no_vig_probabilities_sum_to_one(self):
        probs = no_vig_1x2(2.10, 3.40, 3.60)
        self.assertAlmostEqual(sum(probs), 1.0, places=10)
        self.assertGreater(probs[0], probs[1])

    def test_zero_weight_preserves_model_lambdas(self):
        result = correct_goal_lambdas(1.55, 1.10, 2.10, 3.40, 3.60, market_weight=0.0)
        self.assertAlmostEqual(result["corrected_lambda"]["home"], 1.55)
        self.assertAlmostEqual(result["corrected_lambda"]["away"], 1.10)
        self.assertEqual(result["mode"], "shadow_test_only")

    def test_weight_is_capped_for_shadow_testing(self):
        with self.assertRaises(ValueError):
            correct_goal_lambdas(1.55, 1.10, 2.10, 3.40, 3.60, market_weight=0.50)

    def test_bad_odds_are_rejected(self):
        with self.assertRaises(ValueError):
            no_vig_1x2(1.0, 3.40, 3.60)


if __name__ == "__main__":
    unittest.main()
