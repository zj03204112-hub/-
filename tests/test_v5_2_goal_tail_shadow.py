import unittest
from model_validation.v5_2_goal_tail_shadow import reweight_score_grid, total_goal_bucket

class GoalTailShadowTests(unittest.TestCase):
    def test_bucket_edges(self):
        self.assertEqual(total_goal_bucket(1), "0-1")
        self.assertEqual(total_goal_bucket(2), "2")
        self.assertEqual(total_goal_bucket(4), "3-4")
        self.assertEqual(total_goal_bucket(6), "5-6")
        self.assertEqual(total_goal_bucket(7), "7+")

    def test_normalization_and_tail(self):
        result = reweight_score_grid([(0,0,0.1),(1,0,0.2),(1,1,0.3),(2,1,0.2),(3,2,0.1),(4,3,0.1)])
        self.assertAlmostEqual(result["probability_mass"], 1.0)
        self.assertAlmostEqual(result["p_home"]+result["p_draw"]+result["p_away"], 1.0)
        self.assertEqual(result["validation_status"], "EXPERIMENTAL_NOT_BACKTESTED")
        self.assertFalse(result["production_v5_1_changed"])

    def test_rejects_invalid_mass(self):
        with self.assertRaises(ValueError):
            reweight_score_grid([(0,0,-1.0)])

if __name__ == "__main__":
    unittest.main()
