import unittest

from data_pipeline.goal_total_tail_calibration import TailCalibration, total_goal_bins


class TailCalibrationTests(unittest.TestCase):
    def test_apply_preserves_normalization(self):
        model = TailCalibration((1, 1, 1, 1, 1, 1, 1), 100, 20)
        out = model.apply([.05, .10, .20, .25, .20, .12, .08])
        self.assertAlmostEqual(sum(out), 1.0)
        self.assertTrue(all(x >= 0 for x in out))

    def test_bins_combine_six_and_above(self):
        bins = total_goal_bins([.1, .1, .1, .1, .1, .1, .1, .1, .1, .2])
        self.assertAlmostEqual(bins["6+"], .5)
        self.assertAlmostEqual(sum(bins.values()), 1.0)

    def test_fit_learns_underestimated_high_tail(self):
        # Training-only example: actual 6+ rate is higher than baseline.
        dist = [.10, .15, .20, .20, .15, .10, .10]
        rows = [dist] * 40
        actuals = [2, 3, 1, 4, 2, 5, 3, 2, 4, 6] * 4
        model = TailCalibration.fit(rows, actuals, smoothing=10)
        before = total_goal_bins(dist)["6+"]
        after = total_goal_bins(model.apply(dist))["6+"]
        self.assertGreater(after, before)

    def test_invalid_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            TailCalibration.fit([[.5, .5]], [], smoothing=10)
        with self.assertRaises(ValueError):
            total_goal_bins([0, 0])


if __name__ == "__main__":
    unittest.main()
