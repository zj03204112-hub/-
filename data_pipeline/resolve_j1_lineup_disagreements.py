#!/usr/bin/env python3
"""Resolve the J1 lineup disagreements recorded by the FotMob cross-check.

Decision taken on 2026-11-04: the official J.League Data Site wins. It is the
league's own match record, while FotMob is an aggregator, so a disagreement on who
started is resolved in favour of the official page. No CSV row is rewritten: the
transcription already follows the official page, and the disagreement is recorded
here with the evidence so the decision can be revisited.

Each disagreement is classified before it is decided:

    same_squad_swap       the same 20 players, only the starter/bench split differs
    bench_number_diff     one squad number differs on the bench
    missing_official_page the mapping row has no match_card_id, so no official page
                          exists to compare against (a data gap, not a disagreement)

Usage:
    python data_pipeline/resolve_j1_lineup_disagreements.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
CROSSCHECK = os.path.join(SEASON_DIR, "J1_League_2026_27_lineup_crosscheck_fotmob_20261104.json")
RESOLUTION = os.path.join(SEASON_DIR, "J1_League_2026_27_lineup_disagreements_resolution_20261104.json")
COMPLETION = os.path.join(SEASON_DIR, "J1_League_2026_27_completion_report_20261104.json")


def classify(check: dict) -> str:
    if not check["local_starters"]:
        return "missing_official_page"
    if (sorted(check.get("starters_only_local") or []) == sorted(check.get("bench_only_remote") or [])
            and sorted(check.get("bench_only_local") or []) == sorted(check.get("starters_only_remote") or [])):
        return "same_squad_swap"
    return "bench_number_diff"


def main() -> int:
    report = json.load(open(CROSSCHECK, encoding="utf-8"))
    resolved = []
    for item in report["results"]:
        if item.get("status") != "mismatch":
            continue
        kinds = {}
        for side, check in item["checks"].items():
            if check["starters_ok"] and check["bench_ok"]:
                continue
            kinds[side] = classify(check)
        overall = ("missing_official_page" if "missing_official_page" in kinds.values()
                   else "same_squad_swap" if all(k == "same_squad_swap" for k in kinds.values())
                   else "bench_number_diff")
        entry = {
            "fixture": item["fixture"],
            "kind": overall,
            "sides": kinds,
            "decision": ("needs_mapping" if overall == "missing_official_page"
                         else "official_preferred"),
            "rationale": (
                "Mapping row has no match_card_id, so the official page was never fetched. "
                "This is a data gap in the mapping table, not a provider disagreement."
                if overall == "missing_official_page" else
                "The official J.League Data Site is the league's own match record and outranks an "
                "aggregator, so the official starter/bench split is kept and the CSV is left "
                "unchanged. The disagreement is preserved here for review."
            ),
            "evidence": {
                side: {
                    "starters_only_official": check.get("starters_only_local"),
                    "starters_only_fotmob": check.get("starters_only_remote"),
                    "bench_only_official": check.get("bench_only_local"),
                    "bench_only_fotmob": check.get("bench_only_remote"),
                }
                for side, check in item["checks"].items()
            },
        }
        resolved.append(entry)

    counts = {}
    for entry in resolved:
        counts[entry["decision"]] = counts.get(entry["decision"], 0) + 1

    record = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": "J1 League",
        "season": "2026/27",
        "scope": "lineup disagreements found by the shirt-number cross-check against FotMob",
        "checked_source": "FotMob public match API",
        "authoritative_source": "J.League Data Site (official match record)",
        "policy": "official_preferred: the league record wins over an aggregator, and the "
                  "disagreement is recorded instead of silently overwritten",
        "data_files_modified": [],
        "summary": {"disagreements": len(resolved), **counts},
        "entries": resolved,
    }
    json.dump(record, open(RESOLUTION, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(RESOLUTION, "a", encoding="utf-8").write("\n")

    report["resolution"] = {
        "record": os.path.basename(RESOLUTION),
        "policy": "official_preferred",
        "summary": record["summary"],
        "decided_at": record["generated_at"],
    }
    for item in report["results"]:
        if item.get("status") != "mismatch":
            continue
        match = next(e for e in resolved if e["fixture"] == item["fixture"])
        item["resolution"] = {"decision": match["decision"], "kind": match["kind"]}
    json.dump(report, open(CROSSCHECK, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(CROSSCHECK, "a", encoding="utf-8").write("\n")

    if os.path.exists(COMPLETION):
        completion = json.load(open(COMPLETION, encoding="utf-8"))
        completion["lineup_crosscheck"] = {
            "passed": report["passed"],
            "disagreements": report["failed"],
            "resolution": "official_preferred (see " + os.path.basename(RESOLUTION) + ")",
            "breakdown": record["summary"],
        }
        json.dump(completion, open(COMPLETION, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(COMPLETION, "a", encoding="utf-8").write("\n")

    print(f"disagreements resolved: {len(resolved)} -> {record['summary']}")
    print(f"record: {os.path.relpath(RESOLUTION, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
