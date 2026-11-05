#!/usr/bin/env python3
"""What distinguishes matches where the favourite covers from those where it does not?

Definition used here:
  favourite   the side with the higher 1X2 implied probability (draw excluded)
  covers      穿盘: the final margin plus the jingcai handicap line lands on the favourite's
              side. A push (adjusted margin exactly zero) is 让平 and is reported separately.

The point is to find shared traits, so every trait is reported as a cover rate with its own
sample size, ordered from the highest cover rate down. Differences are only interesting when
the sample is large enough to matter, so bins below 50 matches are dropped.

Usage:
    python data_pipeline/analyze_cover_traits.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "cover_traits.json")
MIN_BIN = 50


def log(message: str) -> None:
    print(message, flush=True)


def devig(values: list[float]) -> list[float] | None:
    if any(v is None or not (v > 1.0) for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def bucket(value: float, edges: list[float], prefix: str = "") -> str:
    for edge in edges:
        if value < edge:
            return f"{prefix}<{edge:g}"
    return f"{prefix}>={edges[-1]:g}"


def main() -> int:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute("""
        SELECT match_date, league, home, away, home_score, away_score, handicap_line,
               result_hhad, win, draw, lost, europe_avg, overunder, asian_hand
        FROM jc_history
        WHERE home_score >= 0 AND away_score >= 0
          AND handicap_line IS NOT NULL AND result_hhad IN ('胜','平','负')""")]
    log(f"matches: {len(rows)}")

    samples = []
    for row in rows:
        probabilities = devig([row["win"], row["draw"], row["lost"]])
        if not probabilities:
            continue
        margin = row["home_score"] - row["away_score"]
        adjusted = margin + row["handicap_line"]
        favourite = "home" if probabilities[0] > probabilities[2] else "away"
        favourite_probability = max(probabilities[0], probabilities[2])
        if adjusted == 0:
            outcome = "push"
            covered = None
        else:
            home_covers = adjusted > 0
            covered = home_covers if favourite == "home" else (not home_covers)
            outcome = "cover" if covered else "miss"
        over_line = 2.5
        if row["overunder"]:
            try:
                over_line = float(row["overunder"].split(",")[1])
            except (ValueError, IndexError):
                pass
        samples.append({
            "league": row["league"], "date": row["match_date"],
            "month": int(row["match_date"][5:7]),
            "line": row["handicap_line"], "favourite": favourite,
            "favourite_probability": favourite_probability,
            "draw_probability": probabilities[1],
            "outcome": outcome, "covered": covered if outcome != "push" else None,
            "favourite_home": favourite == "home",
            "total_line": over_line,
            "score_total": row["home_score"] + row["away_score"],
        })
    usable = samples          # 让平 is a payable outcome in jingcai, so it stays in
    log(f"all matches: {len(usable)}; cover {sum(1 for s in usable if s['covered'] is True)}, "
        f"push {sum(1 for s in usable if s['outcome'] == 'push')}, "
        f"miss {sum(1 for s in usable if s['covered'] is False)}")

    def rate(items: list[dict]) -> dict:
        if len(items) < MIN_BIN:
            return {"n": len(items)}
        covered = sum(1 for s in items if s["covered"] is True)
        pushed = sum(1 for s in items if s["outcome"] == "push")
        return {"n": len(items), "cover_rate": round(covered / len(items), 4),
                "push_rate": round(pushed / len(items), 4),
                "miss_rate": round((len(items) - covered - pushed) / len(items), 4)}

    traits: dict[str, dict[str, dict]] = {}

    def group_rates(key_function):
        groups: dict[str, list[dict]] = defaultdict(list)
        for sample in usable:
            groups[key_function(sample)].append(sample)
        return {key: rate(items) for key, items in groups.items()}

    strength_edges = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    traits["favourite_strength"] = group_rates(
        lambda s: bucket(s["favourite_probability"], strength_edges))
    traits["line_depth"] = group_rates(lambda s: f"{s['line']:+g}")
    traits["favourite_side"] = group_rates(lambda s: s["favourite"])
    traits["draw_price_band"] = group_rates(
        lambda s: bucket(s["draw_probability"], [0.22, 0.25, 0.28]))
    traits["total_goals_line"] = group_rates(
        lambda s: bucket(s["total_line"], [2.4, 2.6, 2.9]))
    traits["season_month"] = group_rates(lambda s: f"{s['month']:02d}")
    traits["league"] = group_rates(lambda s: s["league"])

    payload = {
        "definition": (
            "Cover = the favourite (higher 1X2 implied probability) wins the jingcai handicap. "
            "让平 is a payable third outcome in jingcai, so it is kept in the denominator and "
            "reported as push_rate rather than excluded. Only bins with at least 50 matches "
            "are shown."),
        "matches": len(samples), "usable": len(usable),
        "pushes": len(samples) - len(usable),
        "overall_cover_rate": round(sum(1 for s in usable if s["covered"] is True) / len(usable), 4),
        "overall_push_rate": round(sum(1 for s in usable if s["outcome"] == "push") / len(usable), 4),
        "traits": {
            name: {k: v for k, v in sorted(groups.items(),
                                           key=lambda kv: -kv[1].get("cover_rate", -1))}
            for name, groups in traits.items()},
        "caveat": ("These are descriptive splits, not a model. A spread of a few points "
                   "across bins of similar size is noise."),
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"overall: cover {payload['overall_cover_rate']:.3f}, "
        f"push {payload['overall_push_rate']:.3f}")
    for name, groups in payload["traits"].items():
        log(f"\n  {name}:")
        for key, value in groups.items():
            if value.get("n", 0) >= MIN_BIN:
                log(f"    {key:12s} n={value['n']:5d} 穿盘 {value['cover_rate']:.3f} "
                    f"让平 {value['push_rate']:.3f} 不穿 {value['miss_rate']:.3f}")
    log(f"\nreport: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
