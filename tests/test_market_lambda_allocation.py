import unittest
from experiments.market_lambda_allocation import devig_two_way, infer_total_lambda, poisson_over_probability, market_anchored_prediction

class MarketLambdaTests(unittest.TestCase):
    def test_two_way_probabilities_sum_to_one(self):
        p_over, p_under = devig_two_way(1.8, 2.0)
        self.assertAlmostEqual(p_over + p_under, 1.0)

    def test_total_lambda_matches_over_probability(self):
        target = 0.58
        lam = infer_total_lambda(target, 2.5)
        self.assertAlmostEqual(poisson_over_probability(lam, 2.5), target, places=6)

    def test_pipeline_preserves_total_lambda(self):
        result = market_anchored_prediction(over_odds=1.8, under_odds=2.0, goal_line=2.5,
                                            home_odds=2.1, draw_odds=3.4, away_odds=3.6)
        self.assertAlmostEqual(result["lambda_home"] + result["lambda_away"], result["lambda_total"])
        self.assertFalse(result["production_v5_1_changed"])

if __name__ == "__main__":
    unittest.main()
