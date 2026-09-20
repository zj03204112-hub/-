import json, sqlite3, sys
from pathlib import Path

DB = Path("football_model_database.sqlite")
START, END = "2026-01-01", "2026-09-20"
EXPECTED = {"EPL","LALIGA","BUNDESLIGA","SERIEA","LIGUE1","KLEAGUE1","J1"}

def main():
    if not DB.exists():
        print("ERROR: database file missing")
        return 2
    con = sqlite3.connect(DB)
    errors, warnings = [], []
    comps = dict(con.execute("SELECT competition_code, competition_id FROM competitions"))
    missing = EXPECTED - set(comps)
    if missing:
        errors.append(f"missing competitions: {sorted(missing)}")

    rows = con.execute("""
        SELECT c.competition_code, COUNT(*),
               SUM(CASE WHEN m.status='finished' THEN 1 ELSE 0 END),
               SUM(CASE WHEN r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL THEN 1 ELSE 0 END)
        FROM matches m
        JOIN competitions c ON c.competition_id=m.competition_id
        LEFT JOIN results r ON r.match_id=m.match_id
        GROUP BY c.competition_code ORDER BY c.competition_code
    """).fetchall()

    total = sum(r[1] for r in rows)
    finished = sum(r[2] or 0 for r in rows)
    scored = sum(r[3] or 0 for r in rows)

    dup = con.execute("""
        SELECT competition_id, kickoff, home_team, away_team, COUNT(*)
        FROM matches GROUP BY competition_id, kickoff, home_team, away_team HAVING COUNT(*)>1
    """).fetchall()
    if dup:
        errors.append(f"duplicate fixtures: {len(dup)}")

    # Compare calendar dates, not full timestamps. END is inclusive.
    # This prevents matches played on END after 00:00 from being falsely
    # treated as out-of-scope because their kickoff time is lexically
    # greater than the bare date string.
    bad_dates = con.execute(
        "SELECT COUNT(*) FROM matches WHERE substr(kickoff,1,10) < ? OR substr(kickoff,1,10) > ?",
        (START, END)
    ).fetchone()[0]
    if bad_dates:
        errors.append(f"out-of-scope matches: {bad_dates}")

    missing_results = con.execute("""
        SELECT COUNT(*) FROM matches m
        LEFT JOIN results r ON r.match_id=m.match_id
        WHERE m.status='finished' AND (r.ft_home IS NULL OR r.ft_away IS NULL)
    """).fetchone()[0]
    if missing_results:
        errors.append(f"finished matches without FT score: {missing_results}")

    invalid_result = con.execute("""
        SELECT COUNT(*) FROM results
        WHERE result_1x2 NOT IN ('H','D','A')
           OR ft_home < 0 OR ft_away < 0
    """).fetchone()[0]
    if invalid_result:
        errors.append(f"invalid result rows: {invalid_result}")

    source_rows = con.execute("""
        SELECT sr.source_name, COUNT(*)
        FROM matches m LEFT JOIN source_registry sr ON sr.source_id=m.primary_source_id
        GROUP BY sr.source_name ORDER BY COUNT(*) DESC
    """).fetchall()

    report = {
        "scope": [START, END],
        "total_matches": total,
        "finished_matches": finished,
        "scored_matches": scored,
        "competitions": [dict(code=r[0], matches=r[1], finished=r[2], scored=r[3]) for r in rows],
        "sources": [{"source": r[0] or "UNKNOWN", "matches": r[1]} for r in source_rows],
        "errors": errors,
        "warnings": warnings,
    }
    Path("data/validation_report.json").parent.mkdir(parents=True, exist_ok=True)
    Path("data/validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    con.close()
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())
