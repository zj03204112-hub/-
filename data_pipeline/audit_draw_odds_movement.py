#!/usr/bin/env python3
"""Read-only audit: determine whether sporttery_market contains comparable
pre-match 1X2 draw-odds time series. No DB writes and no model changes.
"""
import json, os, sqlite3
from collections import Counter

DB=os.getenv("FOOTBALL_DB_PATH","football_model_database.sqlite")
OUT=os.getenv("ODDS_MOVEMENT_AUDIT_OUT","draw_odds_movement_audit.json")

def main():
    con=sqlite3.connect(f"file:{DB}?mode=ro",uri=True)
    con.row_factory=sqlite3.Row
    cols=[dict(r) for r in con.execute("PRAGMA table_info(sporttery_market)")]
    names=[r["name"] for r in cols]
    required={"match_id","pool_code","draw_value","captured_at","source_status"}
    if not required.issubset(names):
        raise RuntimeError("sporttery_market missing required columns: "+str(sorted(required-set(names))))
    total=con.execute("SELECT COUNT(*) FROM sporttery_market").fetchone()[0]
    by_pool=[dict(r) for r in con.execute("""
      SELECT pool_code,COUNT(*) rows,COUNT(DISTINCT match_id) matches,
             SUM(CASE WHEN draw_value IS NOT NULL THEN 1 ELSE 0 END) rows_with_draw,
             COUNT(DISTINCT captured_at) distinct_capture_values,
             MIN(captured_at) first_capture,MAX(captured_at) last_capture
      FROM sporttery_market GROUP BY pool_code ORDER BY rows DESC
    """)]
    duplicate_series=[dict(r) for r in con.execute("""
      SELECT pool_code,COUNT(*) match_groups,
             SUM(n) rows_in_multirow_groups,
             MAX(n) max_rows_per_match
      FROM (SELECT pool_code,match_id,COUNT(*) n FROM sporttery_market
            GROUP BY pool_code,match_id HAVING COUNT(*)>1)
      GROUP BY pool_code ORDER BY match_groups DESC
    """)]
    # Count matches with multiple draw prices and the actual range, by pool.
    draw_ranges=[dict(r) for r in con.execute("""
      SELECT pool_code,COUNT(*) multi_draw_match_groups,
             AVG(max_draw-min_draw) avg_draw_odds_range,
             MAX(max_draw-min_draw) max_draw_odds_range
      FROM (
        SELECT pool_code,match_id,COUNT(DISTINCT draw_value) n,
               MIN(draw_value) min_draw,MAX(draw_value) max_draw
        FROM sporttery_market WHERE draw_value IS NOT NULL
        GROUP BY pool_code,match_id HAVING COUNT(DISTINCT draw_value)>1
      ) GROUP BY pool_code ORDER BY multi_draw_match_groups DESC
    """)]
    samples=[dict(r) for r in con.execute("""
      SELECT match_id,pool_code,handicap,home_value,draw_value,away_value,
             captured_at,source_status
      FROM sporttery_market WHERE draw_value IS NOT NULL
      ORDER BY match_id,pool_code,captured_at LIMIT 40
    """)]
    report={
      "status":"READ_ONLY_AUDIT_COMPLETE",
      "database":os.path.basename(DB),
      "sporttery_market_rows":total,
      "columns":cols,
      "by_pool":by_pool,
      "multiple_rows_per_match_pool":duplicate_series,
      "draw_odds_ranges_only_where_multiple_distinct_values_exist":draw_ranges,
      "sample_rows":samples,
      "decision_rule":{
        "usable_for_0_5_movement_test":"Only if the same fixture and same bookmaker/source series has at least two genuinely time-ordered pre-kickoff draw-odds observations with comparable market definitions.",
        "caution":"Multiple rows from different providers, handicap lines, or post-match official result endpoints are not a valid odds-movement time series."
      }
    }
    con.close()
    with open(OUT,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
