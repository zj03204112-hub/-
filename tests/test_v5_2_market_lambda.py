import unittest

from model_validation.v5_2_market_lambda import (
    allocate_lambdas,
    devig_1x2,
    devig_two_way,
    infer_total_lambda,
    poisson_over_probability,
    predict_from_markets,
)


class V52MarketLambdaTests(unittest.TestCase):
    def test_devig_two_way(self):
        over, under = devig_two_way(1.80, 2.00)
        self.assertAlmostEqual(over + under, 1.0)

    def test_devig_1x2(self):
        probs = devig_1x2(2.10, 3.40, 3.60)
        self.assertAlmostEqual(sum(probs), 1.0)
        self.assertGreater(probs[0], probs[2])

    def test_lambda_matches_over_probability(self):
        target = 0.58
        lam = infer_total_lambda(target, 2.5)
        self.assertAlmostEqual(poisson_over_probability(lam, 2.5), target, places=6)

    def test_total_lambda_preserved(self):
        result = allocate_lambdas(2.8, 0.50, 0.27, 0.23, steps=101)
        self.assertAlmostEqual(result["lambda_home"] + result["lambda_away"], 2.8)
        self.assertFalse(result["production_v5_1_changed"])
        self.assertEqual(len(result["distribution"]["top_scores"]), 2)

    def test_end_to_end(self):
        result = predict_from_markets(
            over_odds=1.80, under_odds=2.00, goal_line=2.5,
            home_odds=2.10, draw_odds=3.40, away_odds=3.60,
        )
        self.assertAlmostEqual(result["lambda_home"] + result["lambda_away"], result["lambda_total"])
        self.assertAlmostEqual(sum(result["market"][k] for k in
            ("p_home_devig", "p_draw_devig", "p_away_devig")), 1.0)
        self.assertIn("7+", result["distribution"]["goal_bins"])

    def test_bad_odds_rejected(self):
        with self.assertRaises(ValueError):
            devig_two_way(1.0, 2.0)
        with self.assertRaises(ValueError):
            devig_1x2(2.0, 0.9, 3.0)


if __name__ == "__main__":
    unittest.main()
