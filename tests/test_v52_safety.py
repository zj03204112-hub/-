"""Synthetic regression tests for V5.2 data cutoff and H/D/A diagnostics.
Run: python -m unittest tests/test_v52_safety.py
No production database is required.
"""
import sqlite3
import unittest

from data_pipeline.compare_models import eligible_lineup_projection, classification_metrics

class V52SafetyTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("""CREATE TABLE lineup_projection (
            match_id TEXT, team_side TEXT, attack_delta REAL, defense_delta REAL,
            data_cutoff TEXT, source_status TEXT
        )""")
        self.cutoff = "2026-10-10T00:00:00"

    def tearDown(self):
        self.con.close()

    def test_accepts_verified_projection_at_or_before_cutoff(self):
        self.con.execute("INSERT INTO lineup_projection VALUES (?,?,?,?,?,?)",
                         ("m1","home",0.1,0.05,"2026-10-09T23:59:00","verified"))
        row = eligible_lineup_projection(self.con,"m1","home",self.cutoff)
        self.assertEqual(row,(0.1,0.05))

    def test_rejects_projection_after_cutoff(self):
        self.con.execute("INSERT INTO lineup_projection VALUES (?,?,?,?,?,?)",
                         ("m1","home",0.1,0.05,"2026-10-10T00:01:00","verified"))
        self.assertIsNone(eligible_lineup_projection(self.con,"m1","home",self.cutoff))

    def test_rejects_unverified_source(self):
        self.con.execute("INSERT INTO lineup_projection VALUES (?,?,?,?,?,?)",
                         ("m1","home",0.1,0.05,"2026-10-09T23:00:00","collected"))
        self.assertIsNone(eligible_lineup_projection(self.con,"m1","home",self.cutoff))

    def test_missing_provenance_columns_fails_closed(self):
        self.con.execute("DROP TABLE lineup_projection")
        self.con.execute("CREATE TABLE lineup_projection (match_id TEXT, team_side TEXT, attack_delta REAL, defense_delta REAL)")
        self.con.execute("INSERT INTO lineup_projection VALUES ('m1','home',0.1,0.05)")
        self.assertIsNone(eligible_lineup_projection(self.con,"m1","home",self.cutoff))

    def test_classification_reports_draw_recall_and_confusion(self):
        items = [
            ({"H":0.7,"D":0.2,"A":0.1},"H"),
            ({"H":0.2,"D":0.6,"A":0.2},"D"),
            ({"H":0.4,"D":0.3,"A":0.3},"D"),
            ({"H":0.1,"D":0.2,"A":0.7},"A"),
        ]
        m = classification_metrics(items)
        self.assertEqual(m["draw_recall"],0.5)
        self.assertEqual(m["confusion_matrix_actual_rows_predicted_columns"]["D"]["H"],1)
        self.assertEqual(m["per_class"]["D"]["support"],2)

if __name__ == "__main__":
    unittest.main()
