import unittest

from model_validation.v5_2_big_goal_awareness import (
    assess_big_goal_sensitivity,
    high_total_score_candidates,
)


class BigGoalAwarenessTests(unittest.TestCase):
    def test_returns_separate_sensitivity_and_probability(self):
        result = assess_big_goal_sensitivity(
            current_line=2.5,
            current_over_odds=1.72,
            current_under_odds=2.08,
            opening_line=2.25 + 0.25,
            opening_over_odds=1.95,
            opening_under_odds=1.85,
            lambda_home=1.9,
            lambda_away=1.4,
        )
        self.assertIn(result["sensitivity_tier"], {"高", "偏高", "中性", "低", "偏低"})
        self.assertGreaterEqual(result["big_goal_sensitivity"], 0)
        self.assertLessEqual(result["big_goal_sensitivity"], 100)
        self.assertGreaterEqual(result["p_over_current_line_model"], 0)
        self.assertLessEqual(result["p_over_current_line_model"], 1)
        self.assertEqual(result["validation_status"], "EXPERIMENTAL_NOT_BACKTESTED")

    def test_high_score_tail_surfaces_unusual_scores_separately(self):
        rows = [
            (1, 0, 0.20),
            (2, 1, 0.15),
            (3, 2, 0.03),
            (4, 2, 0.015),
            (5, 1, 0.012),
            (5, 2, 0.008),
            (4, 3, 0.004),
        ]
        out = high_total_score_candidates(rows, min_total_goals=5, limit=10)
        scores = {row["score"] for row in out}
        self.assertIn("3-2", scores)
        self.assertIn("4-2", scores)
        self.assertIn("5-1", scores)
        self.assertIn("5-2", scores)
        self.assertIn("4-3", scores)
        self.assertNotIn("1-0", scores)

    def test_requires_opening_market_as_complete_triplet(self):
        with self.assertRaises(ValueError):
            assess_big_goal_sensitivity(
                current_line=2.5,
                current_over_odds=1.9,
                current_under_odds=1.9,
                lambda_home=1.5,
                lambda_away=1.2,
                opening_line=2.5,
                opening_over_odds=1.9,
            )

    def test_rejects_quarter_line_for_binary_probability(self):
        with self.assertRaises(ValueError):
            assess_big_goal_sensitivity(
                current_line=2.75,
                current_over_odds=1.9,
                current_under_odds=1.9,
                lambda_home=1.5,
                lambda_away=1.2,
            )


if __name__ == "__main__":
    unittest.main()
