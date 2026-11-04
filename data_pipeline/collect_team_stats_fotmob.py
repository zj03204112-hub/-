#!/usr/bin/env python3
"""Collect team level match statistics from FotMob for a competition.

Some competitions (the AFC Champions League Elite for example) are published at
FotMob's ``ratings`` coverage level: no shot map and no xG, but the team statistics
block still carries shots, shots on target, possession, big chances and more. This
script captures that block so those competitions are not left with an empty shot
dimension.

The output is a long table rather than a wide one because the metric set differs
between competitions:

    Date, HomeTeam, AwayTeam, Team, Period, MetricKey, MetricTitle, Value, Source

Usage:
    python data_pipeline/collect_team_stats_fotmob.py --prefix ACLELITE_2026_27
    python data_pipeline/collect_team_stats_fotmob.py --prefix EPL_2026_27 --limit 5
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
FOTMOB = "https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"

HEADER = ["Date", "HomeTeam", "AwayTeam", "Team", "Period", "MetricKey", "MetricTitle",
          "Value", "Source"]


def log(message: str) -> None:
    print(message, flush=True)


def fetch_json(url: str, net, retries: int = 3) -> dict | None:
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                            "AppleWebKit/537.36 Chrome/140 Safari/537.36",
                              "Accept": "application/json",
                              "Referer": "https://www.fotmob.com/",
                              "Accept-Encoding": "gzip"})
            with net.open(request, timeout=40) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, help="fixture prefix, e.g. ACLELITE_2026_27")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fixtures_path = os.path.join(SEASON_DIR, f"{args.prefix}_fixtures.json")
    output_path = os.path.join(SEASON_DIR, f"{args.prefix}_team_stats.csv")
    if not os.path.exists(fixtures_path):
        log(f"missing fixture file: {fixtures_path}")
        return 1

    fixtures = json.load(open(fixtures_path, encoding="utf-8"))
    completed = [f for f in fixtures if f["status"] in ("FT", "AET", "Pen")]
    if args.limit:
        completed = completed[:args.limit]
    log(f"{args.prefix}: {len(completed)} completed fixtures")

    net = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    rows: list[dict] = []
    failed = 0
    metric_counter: dict[str, int] = {}

    for index, fixture in enumerate(completed, start=1):
        if args.dry_run:
            continue
        detail = fetch_json(FOTMOB.format(match_id=fixture["id"]), net)
        if not detail:
            failed += 1
            continue
        periods = (((detail.get("content") or {}).get("stats") or {}).get("Periods")) or {}
        all_stats = ((periods.get("All") or {}).get("stats")) or []
        source = f"https://www.fotmob.com/matches/{fixture['id']}"
        for group in all_stats:
            for metric in group.get("stats") or []:
                values = metric.get("stats") or []
                if len(values) != 2:
                    continue
                key = metric.get("key") or ""
                title = metric.get("title") or ""
                for side, team, value in (("home", fixture["home"], values[0]),
                                          ("away", fixture["away"], values[1])):
                    rows.append({
                        "Date": fixture["date"], "HomeTeam": fixture["home"],
                        "AwayTeam": fixture["away"], "Team": team, "Period": "All",
                        "MetricKey": key, "MetricTitle": title,
                        "Value": "" if value is None else str(value), "Source": source,
                    })
                metric_counter[key] = metric_counter.get(key, 0) + 1
        if index % 20 == 0:
            log(f"  progress {index}/{len(completed)} rows={len(rows)}")

    if args.dry_run:
        log("dry run, nothing written")
        return 0

    # The same metric can appear in more than one statistics group; keep the first.
    deduped = {}
    for row in rows:
        deduped.setdefault((row["Date"], row["HomeTeam"], row["AwayTeam"], row["Team"],
                            row["Period"], row["MetricKey"]), row)
    rows = list(deduped.values())
    rows.sort(key=lambda r: (r["Date"], r["HomeTeam"], r["AwayTeam"], r["Team"], r["MetricKey"]))
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "table": os.path.basename(output_path),
        "competition_prefix": args.prefix,
        "fixtures": len(completed),
        "rows": len(rows),
        "failed": failed,
        "distinct_metrics": len(metric_counter),
        "top_metrics": sorted(metric_counter.items(), key=lambda kv: -kv[1])[:12],
        "note": "Long format because the metric set differs per competition; filter on "
                "MetricKey for analysis, for example total_shots or BallPossesion.",
    }
    report_path = os.path.join(SEASON_DIR, f"{args.prefix}_team_stats_report_"
                                          f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.json")
    json.dump(report, open(report_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(report_path, "a", encoding="utf-8").write("\n")
    log(f"rows: {len(rows)}, metrics: {len(metric_counter)}, failed: {failed}")
    log(f"output: {os.path.relpath(output_path, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
