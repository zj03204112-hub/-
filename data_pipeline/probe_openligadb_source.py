#!/usr/bin/env python3
"""Probe OpenLigaDB without modifying the production SQLite database.

Downloads one league-season response, validates the response shape, and writes
an auditable JSON report. It deliberately does NOT import results as verified:
OpenLigaDB is community-maintained, so result rows must be cross-checked before
being inserted into own_results (whose schema only accepts verified results).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.openligadb.de/getmatchdata"

def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "football-db-audit/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        return response.read()

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--league", default="bl1", help="OpenLigaDB league shortcut (default bl1)")
    p.add_argument("--season", type=int, default=2026, help="season start year")
    p.add_argument("--out", default="openligadb_probe_report.json")
    args = p.parse_args()
    url = f"{BASE}/{args.league}/{args.season}"
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "OpenLigaDB",
        "url": url,
        "league": args.league,
        "season": args.season,
        "status": "failed",
        "total_records": 0,
        "finished_records": 0,
        "records_with_full_time_score": 0,
        "duplicate_provider_ids": [],
        "payload_sha256": None,
        "limitations": [
            "OpenLigaDB is community-maintained, not an official league feed.",
            "This probe does not write to SQLite or declare scores verified.",
            "Provider event IDs are provider-scoped and must not be treated as global match IDs.",
        ],
    }
    try:
        raw = fetch(url)
        report["payload_sha256"] = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array")
        seen = set()
        dupes = set()
        finished = 0
        scored = 0
        for row in data:
            if not isinstance(row, dict):
                continue
            mid = row.get("matchID")
            if mid is not None:
                key = str(mid)
                if key in seen:
                    dupes.add(key)
                seen.add(key)
            if row.get("matchIsFinished") is True:
                finished += 1
                results = row.get("matchResults") or []
                # OpenLigaDB may return multiple result types; require a result explicitly
                # labelled as the final/full-time result, never infer from a partial score.
                has_ft = any(
                    isinstance(r, dict)
                    and str(r.get("resultTypeID", "")) in {"2", "3"}
                    and isinstance(r.get("pointsTeam1"), int)
                    and isinstance(r.get("pointsTeam2"), int)
                    for r in results
                )
                if has_ft:
                    scored += 1
        report.update({
            "status": "ok",
            "total_records": len(data),
            "finished_records": finished,
            "records_with_full_time_score": scored,
            "duplicate_provider_ids": sorted(dupes),
        })
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        report["error"] = str(exc)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1

if __name__ == "__main__":
    raise SystemExit(main())
