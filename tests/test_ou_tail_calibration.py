import unittest

from experiments.ou_tail_calibration import (
    TOTAL_GOAL_BINS,
    asian_total_settlement,
    fit_bin_calibrator,
)


class CalibrationTests(unittest.TestCase):
    def test_total_bins_cover_tail(self):
        self.assertEqual([TOTAL_GOAL_BINS[i] for i in range(5)],
                         [(0, 1), (2, 2), (3, 4), (5, 6), (7, None)])

    def test_calibrated_pmf_is_normalized(self):
        pmfs = [[0.1, 0.2, 0.3, 0.2, 0.1, 0.05, 0.03, 0.02]] * 40
        actual = [5] * 12 + [2] * 10 + [3] * 8 + [1] * 10
        model = fit_bin_calibrator(pmfs, actual, TOTAL_GOAL_BINS, smoothing=5)
        out = model.apply(pmfs[0])
        self.assertAlmostEqual(sum(out), 1.0, places=10)
        self.assertTrue(all(p >= 0 for p in out))
        self.assertGreater(model.weights[3], 1.0)

    def test_asian_quarter_line(self):
        self.assertEqual(asian_total_settlement(3, 2.75), "half_win")
        self.assertEqual(asian_total_settlement(2, 2.75), "loss")
        self.assertEqual(asian_total_settlement(4, 2.75), "win")
        self.assertEqual(asian_total_settlement(3, 3.0), "push")
        self.assertEqual(asian_total_settlement(2, 2.75, "under"), "half_win")
        self.assertEqual(asian_total_settlement(4, 2.75, "under"), "loss")


if __name__ == "__main__":
    unittest.main()
