#!/usr/bin/env python3
"""Settle the prediction ledger against actual results, then report on it.

Two jobs, one script:

  --review   look up each pending prediction in the jingcai archive (jc_history, matched on
             date within a day and on the match number) and fill in the score, the realised
             outcome in the same vocabulary as the pick, and whether it was correct

  (default)  print the record: overall hit rate, hit rate by market, by confidence band, by
             league, and the running summary per day

The point is that the numbers are computed from stored predictions, not reconstructed after the
fact, so they cannot drift toward whatever story is convenient.

Usage:
    python data_pipeline/review_ledger.py --review
    python data_pipeline/review_ledger.py
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")

SPF_TO_PICK = {"胜": "主胜", "平": "平局", "负": "客胜"}
HHAD_TO_PICK = {"胜": "让胜", "平": "让平", "负": "让负"}
MARKET_LABEL = {"spf": "胜平负", "hhad": "让球", "hhad_draw": "让平(专项)"}


def log(message: str) -> None:
    print(message, flush=True)


def review(connection) -> int:
    pending = [dict(r) for r in connection.execute(
        """SELECT prediction_id, match_date, match_num, market FROM prediction_ledger
           WHERE correct IS NULL""")]
    if not pending:
        log("nothing pending")
        return 0

    settled = 0
    for row in pending:
        day = date.fromisoformat(row["match_date"])
        found = None
        for offset in (0, 1, -1, 2, -2):
            key = (day + timedelta(days=offset)).isoformat()
            found = connection.execute(
                """SELECT home_score, away_score, result_spf, result_hhad FROM jc_history
                   WHERE match_date = ? AND matchnum = ? AND result_spf IN ('胜','平','负')""",
                (key, row["match_num"])).fetchone()
            if found:
                break
        if not found or found["result_spf"] not in SPF_TO_PICK:
            continue
        score = f"{found['home_score']}-{found['away_score']}"
        if row["market"] == "spf":
            outcome = SPF_TO_PICK[found["result_spf"]]
        else:
            outcome = HHAD_TO_PICK.get(found["result_hhad"]) if found["result_hhad"] else None
            if outcome is None:
                continue
        pick = connection.execute(
            "SELECT pick FROM prediction_ledger WHERE prediction_id = ?",
            (row["prediction_id"],)).fetchone()["pick"]
        connection.execute(
            """UPDATE prediction_ledger
               SET result_score = ?, result_outcome = ?, correct = ?, reviewed_at = datetime('now')
               WHERE prediction_id = ?""",
            (score, outcome, int(pick == outcome), row["prediction_id"]))
        settled += 1
    connection.commit()
    log(f"settled {settled} of {len(pending)} pending predictions")
    return settled


def full_table(connection, limit_days: int = 1) -> None:
    """Print every settled prediction for the most recent match days."""
    days = [r["match_date"] for r in connection.execute(
        """SELECT DISTINCT match_date FROM prediction_ledger
           WHERE correct IS NOT NULL ORDER BY match_date DESC LIMIT ?""", (limit_days,))]
    if not days:
        return
    log("\n逐场复盘:")
    for day in sorted(days):
        rows = list(connection.execute(
            """SELECT match_num, home, away, market, pick, pick_probability,
                      result_score, result_outcome, correct
               FROM prediction_ledger
               WHERE match_date = ? AND correct IS NOT NULL
               ORDER BY match_num, market""", (day,)))
        if not rows:
            continue
        log(f"  {day}:")
        for row in rows:
            mark = "OK " if row["correct"] else "MISS"
            market = {"spf": "胜平负", "hhad": "让球", "hhad_draw": "让平"}[row["market"]]
            log(f"    {row['match_num']:8s} {row['home'][:7] + '-' + row['away'][:7]:15s} "
                f"{market:4s} {row['pick']:3s} {100 * row['pick_probability']:5.1f}%  "
                f"{row['result_score']:5s} {row['result_outcome']:3s} {mark}")
    log("")


def report(connection) -> None:
    total = connection.execute("SELECT COUNT(*) FROM prediction_ledger").fetchone()[0]
    settled = connection.execute(
        "SELECT COUNT(*) FROM prediction_ledger WHERE correct IS NOT NULL").fetchone()[0]
    log(f"ledger: {total} rows, {settled} settled, {total - settled} pending\n")
    if not settled:
        return

    def summarise(title: str, query: str) -> None:
        rows = list(connection.execute(query))
        if not rows:
            return
        log(title)
        for row in rows:
            rate = row["hits"] / row["n"] if row["n"] else 0
            log(f"  {str(row['key']):14s} {row['n']:5d} 场  {100 * rate:5.1f}%  期望 {100 * row['expected']:.1f}%")
        log("")

    summarise("按玩法:", """
        SELECT market AS key, COUNT(*) AS n, SUM(correct) AS hits,
               AVG(pick_probability) AS expected
        FROM prediction_ledger WHERE correct IS NOT NULL GROUP BY market ORDER BY n DESC""")
    summarise("按置信度:", """
        SELECT confidence AS key, COUNT(*) AS n, SUM(correct) AS hits,
               AVG(pick_probability) AS expected
        FROM prediction_ledger WHERE correct IS NOT NULL GROUP BY confidence""")
    summarise("按日期:", """
        SELECT match_date AS key, COUNT(*) AS n, SUM(correct) AS hits,
               AVG(pick_probability) AS expected
        FROM prediction_ledger WHERE correct IS NOT NULL GROUP BY match_date
        ORDER BY match_date DESC LIMIT 10""")
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--days", type=int, default=1,
                        help="how many recent match days to print in the per-match review")
    args = parser.parse_args()

    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    if args.review:
        review(connection)
    report(connection)
    full_table(connection, limit_days=args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
