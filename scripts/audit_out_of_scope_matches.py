#!/usr/bin/env python3
"""Read-only diagnostic for matches outside the declared collection scope."""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

DB = Path("football_model_database.sqlite")
OUT = Path("data/out_of_scope_audit.json")
START, END = "2022-01-01", datetime.now(ZoneInfo("Asia/Singapore")).date().isoformat()

def main():
    if not DB.exists():
        raise SystemExit("database missing")
    con = sqlite3.connect(DB)
    rows = con.execute("""
      SELECT m.match_id, c.competition_code, m.kickoff, m.home_team, m.away_team,
             m.status, m.source_status, m.primary_source_id,
             substr(m.kickoff,1,10) AS calendar_date
      FROM matches m LEFT JOIN competitions c ON c.competition_id=m.competition_id
      WHERE substr(m.kickoff,1,10) < ? OR substr(m.kickoff,1,10) > ?
      ORDER BY m.kickoff, c.competition_code, m.home_team, m.away_team
    """, (START, END)).fetchall()
    records = [dict(match_id=r[0], competition_code=r[1], kickoff=r[2],
                    home_team=r[3], away_team=r[4], status=r[5],
                    source_status=r[6], primary_source_id=r[7], calendar_date=r[8])
               for r in rows]
    report = {"scope":{"start":START,"end_inclusive":END},
              "out_of_scope_count":len(records),"records":records,
              "policy":"Read-only. No record is deleted or rewritten by this audit."}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    con.close()

if __name__ == "__main__":
    main()
