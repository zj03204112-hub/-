import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_pipeline"))
import v5_1_ou_tail_calibration_experiment as exp


class OUTailCalibrationTests(unittest.TestCase):
    def test_matrix_is_normalized(self):
        m = exp.matrix(1.7, 1.1)
        self.assertAlmostEqual(sum(map(sum, m)), 1.0, places=10)
        self.assertTrue(all(v >= 0 for row in m for v in row))

    def test_de_vig_over_probability(self):
        p = exp.implied_over_probability(2.0, 2.0)
        self.assertAlmostEqual(p, 0.5, places=10)
        self.assertGreater(exp.implied_over_probability(1.7, 2.2), 0.5)

    def test_asian_quarter_line_handles_half_win_loss(self):
        totals = {0: 0.25, 1: 0.25, 2: 0.25, 3: 0.25}
        p25 = exp.over_share_for_line(totals, 2.5)
        p225 = exp.over_share_for_line(totals, 2.25)
        p275 = exp.over_share_for_line(totals, 2.75)
        self.assertAlmostEqual(p25, 0.25, places=10)
        self.assertGreater(p225, p25)
        self.assertLess(p275, p25 + 0.01)

    def test_ou_adjustment_preserves_home_away_lambda_ratio(self):
        market = {"ou_line": 3.5, "over_odds": 1.8, "under_odds": 2.0}
        lh, la = 1.8, 1.0
        nh, na, scale = exp.apply_ou_market(lh, la, market)
        self.assertGreater(scale, 0)
        self.assertAlmostEqual(nh / na, lh / la, places=10)

    def test_margin_calibration_keeps_probability_mass(self):
        m1 = exp.matrix(1.6, 1.2)
        rows = [
            {"actual_home": 3, "actual_away": 0, "market_matrix": m1},
            {"actual_home": 1, "actual_away": 1, "market_matrix": m1},
            {"actual_home": 2, "actual_away": 1, "market_matrix": m1},
            {"actual_home": 0, "actual_away": 2, "market_matrix": m1},
        ]
        factors = exp.fit_margin_factors(rows)
        calibrated = exp.apply_margin_factors(m1, factors)
        self.assertAlmostEqual(sum(map(sum, calibrated)), 1.0, places=10)
        self.assertTrue(all(x > 0 for x in factors))


    def test_market_json_aliases(self):
        market = exp.normalize_market_json({
            "asian_total_line": 2.75, "over_odds": 1.91, "under_odds": 1.95
        })
        self.assertEqual(market["ou_line"], 2.75)
        self.assertAlmostEqual(market["over_odds"], 1.91)

    def test_market_trace_requires_provider_timestamp_before_cutoff(self):
        import json
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.execute("""CREATE TABLE market_trace (
            match_id TEXT, market_type TEXT, provider_event_timestamp_utc TEXT,
            timestamp_quality TEXT, normalized_json TEXT)""")
        con.execute("INSERT INTO market_trace VALUES (?,?,?,?,?)", (
            "m1", "asian_total", "2026-10-21T11:00:00Z", "PROVIDER_TIMESTAMPED",
            json.dumps({"asian_total_line": 2.5, "over_odds": 1.9, "under_odds": 1.9})
        ))
        con.execute("INSERT INTO market_trace VALUES (?,?,?,?,?)", (
            "m1", "asian_total", "2026-10-21T13:00:00Z", "PROVIDER_TIMESTAMPED",
            json.dumps({"asian_total_line": 3.5, "over_odds": 1.8, "under_odds": 2.0})
        ))
        market = exp.fetch_t12h_market(
            con, {"market_trace"}, "m1", "2026-10-21T12:00:00"
        )
        self.assertEqual(market["ou_line"], 2.5)
        con.close()

    def test_evaluation_exposes_required_metrics(self):
        m = exp.matrix(1.4, 1.0)
        row = {"actual_home": 1, "actual_away": 0, "base_matrix": m}
        metrics = exp.evaluate([row], "base_matrix")
        for key in ("hda_accuracy", "hda_brier", "hda_logloss",
                    "score_probability_logloss", "total_goals_distribution_logloss",
                    "expected_total_goals_mae"):
            self.assertIn(key, metrics)


if __name__ == "__main__":
    unittest.main()
