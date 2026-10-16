import unittest
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("leisu_web_scraper", ROOT / "data_pipeline" / "leisu_public_web_scraper.py")
scraper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scraper)

class PublicPageParserTests(unittest.TestCase):
    def test_extracts_detail_id_without_inventing_fields(self):
        html = b'<html><body><a href="/detail-1234567">Data</a><a href="/other">Other</a></body></html>'
        rows = scraper.parse_list(html, "https://live.leisu.com/wanchang")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "1234567")
        self.assertEqual(rows[0]["home"], None)
        self.assertEqual(rows[0]["handicap_odds"], None)
        self.assertEqual(rows[0]["status"], "DISCOVERED_NEEDS_DETAIL_PARSE")

    def test_only_parses_explicit_full_time_score(self):
        html = "全场 2-1 主队 客队".encode()
        row = scraper.parse_detail(html, "123", "https://live.leisu.com/detail-123")
        self.assertEqual(row["score_ft"], [2, 1])
        html2 = "主队近期 2-1 客队近期".encode()
        row2 = scraper.parse_detail(html2, "123", "https://live.leisu.com/detail-123")
        self.assertIsNone(row2["score_ft"])

if __name__ == "__main__":
    unittest.main()
