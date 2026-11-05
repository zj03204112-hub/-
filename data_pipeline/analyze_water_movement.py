#!/usr/bin/env python3
"""Does water movement predict the handicap result?

The radar snapshot stores each match's Asian line and home water at opening and at the
snapshot moment. This tests whether that movement carries information:

  follow the water   bet home when the home water dropped (money on home), away otherwise
  follow the line    bet home when the line moved toward home, away otherwise
  radar signal       hit rate of the page's own signal ("主不败" / "客不败")

A cover is decided against the current line, i.e. the latest one in the snapshot. Pushes are
excluded. With one or two days of data the sample is tiny, so the report prints the count
alongside every number and says so explicitly; the point is to have the harness ready for the
data that accumulates daily.

Usage:
    python data_pipeline/analyze_water_movement.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "water_movement.json")


def log(message: str) -> None:
    print(message, flush=True)


def cover(margin: float, line: float) -> str | None:
    adjusted = margin + line
    if adjusted > 0:
        return "home"
    if adjusted < 0:
        return "away"
    return None


def summarise(hits: int, total: int, label: str) -> dict:
    if not total:
        return {"rule": label, "n": 0}
    rate = hits / total
    return {"rule": label, "n": total, "hit_rate": round(rate, 4),
            "z_vs_coinflip": round((rate - 0.5) / math.sqrt(0.25 / total), 2)}


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute("""
        SELECT * FROM radar_match
        WHERE home_score IS NOT NULL AND away_score IS NOT NULL
          AND ah_line_open IS NOT NULL AND ah_line_cur IS NOT NULL
          AND ah_water_open IS NOT NULL AND ah_water_cur IS NOT NULL""")]
    log(f"matches with a result and both water readings: {len(rows)}")
    if not rows:
        return 1

    hits = defaultdict(int)
    totals = defaultdict(int)
    deltas = []
    records = []

    for row in rows:
        margin = row["home_score"] - row["away_score"]
        result = cover(margin, row["ah_line_cur"])
        if result is None:
            continue
        delta_water = row["ah_water_cur"] - row["ah_water_open"]
        delta_line = row["ah_line_cur"] - row["ah_line_open"]
        deltas.append(delta_water)

        if abs(delta_water) > 1e-9:
            pick = "home" if delta_water < 0 else "away"
            totals["follow_water"] += 1
            hits["follow_water"] += int(pick == result)
        if abs(delta_line) > 1e-9:
            pick = "home" if delta_line < 0 else "away"
            totals["follow_line"] += 1
            hits["follow_line"] += int(pick == result)
        if abs(delta_water) > 0.03:               # only material moves
            pick = "home" if delta_water < 0 else "away"
            totals["follow_water_gt_003"] += 1
            hits["follow_water_gt_003"] += int(pick == result)

        signal = row["signal_pred"] or ""
        if signal in ("主不败", "客不败"):
            # signal is two-way (win or draw); compare against a two-way settle
            ok = (signal == "主不败" and margin >= 0) or (signal == "客不败" and margin <= 0)
            totals["radar_signal"] += 1
            hits["radar_signal"] += int(ok)

        records.append({"match": f"{row['home']}-{row['away']}", "margin": margin,
                        "line_cur": row["ah_line_cur"], "delta_water": round(delta_water, 3),
                        "delta_line": round(delta_line, 3), "result": result})

    payload = {
        "definition": (
            "Water and line movement versus the handicap result. A cover is decided against "
            "the current line, pushes excluded. The sample is one or two trading days, so "
            "numbers are descriptive and every rule reports its own n."),
        "matches": len(records),
        "mean_delta_water": round(statistics.mean(deltas), 4) if deltas else None,
        "share_water_dropped": round(sum(1 for d in deltas if d < 0) / len(deltas), 4)
        if deltas else None,
        "rules": [summarise(hits[k], totals[k], k) for k in
                  ("follow_water", "follow_water_gt_003", "follow_line", "radar_signal")],
        "records": records[:200],
        "caveat": "This is a harness, not a result: the sample is far too small to conclude.",
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    for item in payload["rules"]:
        if item.get("n"):
            log(f"  {item['rule']:22s} n={item['n']:3d} 命中 {item['hit_rate']:.3f} "
                f"(z={item['z_vs_coinflip']:+.2f})")
        else:
            log(f"  {item['rule']:22s} 无样本")
    log(f"  水位下降的场次占比: {payload['share_water_dropped']}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
