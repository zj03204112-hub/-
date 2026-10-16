import hashlib, json, sqlite3, tempfile, unittest
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("v52_live", ROOT / "data_pipeline" / "run_v52_live_prediction.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)

class TraceStoreTests(unittest.TestCase):
    def test_sha_is_stable(self):
        self.assertEqual(v.sha({"b":2,"a":1}), v.sha({"a":1,"b":2}))
    def test_trace_tables_append_only(self):
        old = v.TRACE_DB
        with tempfile.TemporaryDirectory() as td:
            v.TRACE_DB = Path(td) / "trace.sqlite"
            c = v.init_trace()
            c.execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("p","m","2026-10-16T00:00:00+00:00","2026-10-16T03:00:00+00:00","V5.2","sha",1.2,1.1,.4,.3,.3,"1-0",.1,"1-1",.08,"{}","sha"))
            c.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE predictions SET p_home=.5 WHERE prediction_id='p'")
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("DELETE FROM predictions WHERE prediction_id='p'")
            c.close()
            v.TRACE_DB = old

if __name__ == "__main__":
    unittest.main()
