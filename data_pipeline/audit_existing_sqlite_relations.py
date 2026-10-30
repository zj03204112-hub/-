#!/usr/bin/env python3
"""Read-only relational integrity audit for football_model_database.sqlite."""
import json, sqlite3, hashlib, os
p=os.environ.get("DB_PATH","football_model_database.sqlite")
if not os.path.isfile(p):
    raise SystemExit(f"DB_NOT_FOUND: {p}")
con=sqlite3.connect(f"file:{p}?mode=ro",uri=True)
con.row_factory=sqlite3.Row
def q(sql):
    return [dict(r) for r in con.execute(sql)]
out={"file":p,"bytes":os.path.getsize(p)}
out["integrity"]=q("PRAGMA integrity_check")
out["counts"]={t:q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in ["matches","results","competitions","seasons","provider_event_map","sporttery_market","prediction_snapshot"]}
out["join"] = q("""SELECT COUNT(*) matches,
 SUM(CASE WHEN r.match_id IS NULL THEN 1 ELSE 0 END) missing_result,
 SUM(CASE WHEN r.match_id IS NOT NULL THEN 1 ELSE 0 END) matched_result,
 SUM(CASE WHEN r.match_id IS NOT NULL AND (r.ft_home IS NULL OR r.ft_away IS NULL) THEN 1 ELSE 0 END) matched_without_ft
 FROM matches m LEFT JOIN results r ON r.match_id=m.match_id""")[0]
out["orphan_results"]=q("SELECT COUNT(*) n FROM results r LEFT JOIN matches m ON m.match_id=r.match_id WHERE m.match_id IS NULL")[0]["n"]
out["duplicate_match_ids"]=q("SELECT COUNT(*) n FROM (SELECT match_id FROM matches GROUP BY match_id HAVING COUNT(*)>1)")[0]["n"]
out["by_competition"]=q("""SELECT c.competition_code,c.competition_name,COUNT(*) matches,
 SUM(CASE WHEN r.match_id IS NOT NULL THEN 1 ELSE 0 END) with_result,
 SUM(CASE WHEN r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL THEN 1 ELSE 0 END) with_ft,
 MIN(m.kickoff) first_kickoff,MAX(m.kickoff) last_kickoff
 FROM matches m LEFT JOIN results r ON r.match_id=m.match_id
 LEFT JOIN competitions c ON c.competition_id=m.competition_id
 GROUP BY c.competition_code,c.competition_name ORDER BY c.competition_code""")
out["result_status"]=q("SELECT source_status,COUNT(*) n FROM results GROUP BY source_status")
print(json.dumps(out,ensure_ascii=False,indent=2))
