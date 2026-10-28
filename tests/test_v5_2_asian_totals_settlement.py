import unittest

from model_validation.v5_2_asian_totals_settlement import (
    poisson_total_pmf,
    settle_probabilities,
    settlement_profit,
)


class AsianTotalsSettlementTests(unittest.TestCase):
    def test_225_total_two_is_half_loss_for_over_and_half_win_for_under(self):
        self.assertAlmostEqual(settlement_profit(2, 2.25, "over", 1.90), -0.5)
        self.assertAlmostEqual(settlement_profit(2, 2.25, "under", 1.90), 0.45)

    def test_275_total_three_is_half_win_for_over_and_half_loss_for_under(self):
        self.assertAlmostEqual(settlement_profit(3, 2.75, "over", 1.90), 0.45)
        self.assertAlmostEqual(settlement_profit(3, 2.75, "under", 1.90), -0.5)

    def test_225_boundaries(self):
        self.assertEqual(settlement_profit(1, 2.25, "over", 2.0), -1.0)
        self.assertEqual(settlement_profit(3, 2.25, "over", 2.0), 1.0)
        self.assertEqual(settlement_profit(1, 2.25, "under", 2.0), 1.0)
        self.assertEqual(settlement_profit(3, 2.25, "under", 2.0), -1.0)

    def test_275_boundaries(self):
        self.assertEqual(settlement_profit(2, 2.75, "over", 2.0), -1.0)
        self.assertEqual(settlement_profit(4, 2.75, "over", 2.0), 1.0)
        self.assertEqual(settlement_profit(2, 2.75, "under", 2.0), 1.0)
        self.assertEqual(settlement_profit(4, 2.75, "under", 2.0), -1.0)

    def test_probability_output(self):
        pmf = poisson_total_pmf(3.1)
        result = settle_probabilities(line=2.75, side="over", odds=1.90,
                                      total_goal_probabilities=pmf)
        self.assertAlmostEqual(sum(result["settlement_probabilities"].values()), 1.0, places=8)
        self.assertGreaterEqual(result["expected_net_return_per_unit_stake"], -1.0)
        self.assertLessEqual(result["expected_net_return_per_unit_stake"], 0.9)


if __name__ == "__main__":
    unittest.main()
