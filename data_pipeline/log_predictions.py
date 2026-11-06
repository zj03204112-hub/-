#!/usr/bin/env python3
"""Write tonight's predictions into the ledger.

The ledger is the piece the repository has been missing: a permanent record of what was
predicted before kick-off, so the hit rate can be measured rather than argued about.

Three markets are recorded per fixture, each in its own row:

  spf        胜平负, picking the stronger side
  hhad       让球三选一, picking the most likely of the three
  hhad_draw  让平, recorded only when its probability clears the 0.26 threshold

Rows are keyed by (match_date, match_num, market), so re-running on the same day updates
nothing and cannot inflate the record. Review is filled in later by review_ledger.py.

Usage:
    python data_pipeline/log_predictions.py
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from devig import power_devig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
DRAW_THRESHOLD = 0.26
LABELS = ("让胜", "让平", "让负")
SPF_LABELS = ("主胜", "平局", "客胜")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prediction_ledger (
  prediction_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at        TEXT NOT NULL,
  match_date        TEXT NOT NULL,
  match_num         TEXT NOT NULL,
  league            TEXT,
  home              TEXT,
  away              TEXT,
  kickoff           TEXT,
  market            TEXT NOT NULL,
  pick              TEXT NOT NULL,
  pick_probability  REAL,
  line              REAL,
  confidence        TEXT,
  devig             TEXT DEFAULT 'power',
  result_score      TEXT,
  result_outcome    TEXT,
  correct           INTEGER,
  reviewed_at       TEXT,
  note              TEXT,
  UNIQUE(match_date, match_num, market)
);
CREATE INDEX IF NOT EXISTS idx_ledger_date ON prediction_ledger(match_date);
CREATE INDEX IF NOT EXISTS idx_ledger_pending ON prediction_ledger(correct);
"""


def log(message: str) -> None:
    print(message, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", default="")
    args = parser.parse_args()

    connection = sqlite3.connect(DB)
    connection.executescript(SCHEMA)

    url = API + "?" + urllib.parse.urlencode({"poolCode": "had,hhad", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    written = 0
    fixtures = 0
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            league = item.get("leagueAbbName") or ""
            if args.league and args.league not in league:
                continue
            had, hhad = item.get("had") or {}, item.get("hhad") or {}
            try:
                spf = power_devig([float(had["h"]), float(had["d"]), float(had["a"])])
                three = power_devig([float(hhad["h"]), float(hhad["d"]), float(hhad["a"])])
                line = float(hhad.get("goalLineValue") or hhad.get("goalLine"))
            except (KeyError, TypeError, ValueError):
                continue
            if not spf or not three:
                continue
            fixtures += 1
            match_date = (item.get("matchDate") or "").split(" ")[0]
            match_num = item.get("matchNumStr") or ""
            common = (now, match_date, match_num, league,
                      item.get("homeTeamAbbName"), item.get("awayTeamAbbName"),
                      f"{item.get('matchDate')} {item.get('matchTime')}")

            favourite = 0 if spf[0] > spf[2] else 2
            spf_gap = abs(spf[0] - spf[2])
            spf_confidence = "high" if spf_gap >= 0.30 else ("mid" if spf_gap >= 0.15 else "low")
            connection.execute("""
                INSERT OR IGNORE INTO prediction_ledger
                (created_at, match_date, match_num, league, home, away, kickoff,
                 market, pick, pick_probability, line, confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                common + ("spf", SPF_LABELS[favourite], round(spf[favourite], 4), None,
                          spf_confidence))
            written += 1

            order = sorted(range(3), key=lambda i: -three[i])
            gap = three[order[0]] - three[order[1]]
            confidence = "high" if gap >= 0.15 else ("mid" if gap >= 0.06 else "low")
            connection.execute("""
                INSERT OR IGNORE INTO prediction_ledger
                (created_at, match_date, match_num, league, home, away, kickoff,
                 market, pick, pick_probability, line, confidence)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                common + ("hhad", LABELS[order[0]], round(three[order[0]], 4), line,
                          confidence))
            written += 1

            if three[1] >= DRAW_THRESHOLD:
                connection.execute("""
                    INSERT OR IGNORE INTO prediction_ledger
                    (created_at, match_date, match_num, league, home, away, kickoff,
                     market, pick, pick_probability, line, confidence, note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    common + ("hhad_draw", "让平", round(three[1], 4), line, "watch",
                              f"让平概率 {100 * three[1]:.1f}% 达 {100 * DRAW_THRESHOLD:.0f}% 门槛"))
                written += 1

    connection.commit()
    total = connection.execute("SELECT COUNT(*) FROM prediction_ledger").fetchone()[0]
    pending = connection.execute(
        "SELECT COUNT(*) FROM prediction_ledger WHERE correct IS NULL").fetchone()[0]
    log(f"fixtures seen: {fixtures}, rows offered: {written}")
    log(f"ledger now holds {total} rows, {pending} still awaiting results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
