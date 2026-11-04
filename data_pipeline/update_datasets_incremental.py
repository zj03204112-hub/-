#!/usr/bin/env python3
"""Incrementally extend the league layers with newly completed matches.

Re-running a full collector costs ten to twenty minutes per competition, so this
script only handles the fixtures that finished since the last stored match:

  1. rescan the calendar around the stored window so newly scheduled or newly finished
     fixtures enter the fixture cache
  2. work out which completed fixtures have no batch JSON yet (matched by the provider
     match id inside ``official_match_url``, so team-name language never matters)
  3. fetch those matches only, reuse the league's own ``build_match`` function, append
     the rows to the six CSV tables and write the batch JSON
  4. refresh SQLite through data_pipeline/import_datasets_to_sqlite.py

Leagues collected from FotMob share the same ``build_match`` shape and are fully
automatic. LaLiga runs its own incremental collector. J1 is reported as needing a
manual pass because its fixtures are keyed by an official match_card_id that has to be
looked up first.

Usage:
    python data_pipeline/update_datasets_incremental.py --dry-run
    python data_pipeline/update_datasets_incremental.py
"""

from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PIPELINE = os.path.join(REPO_ROOT, "data_pipeline")
FOTMOB_MATCH = "https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"

# code, prefix, fotmob league ids, collector module holding build_match
FOTMOB_LEAGUES = [
    ("EPL", "EPL_2026_27", [47], "collect_epl_fotmob"),
    ("BUNDESLIGA", "BUNDESLIGA_2026_27", [54], "collect_bundesliga_fotmob"),
    ("SERIEA", "SERIEA_2026_27", [55], "collect_seriea_fotmob"),
    ("LIGUE1", "LIGUE1_2026_27", [53], "collect_ligue1_fotmob"),
    ("EREDIVISIE", "EREDIVISIE_2026_27", [937276], "collect_eredivisie_fotmob"),
    ("UCL", "UCL_2026_27", [943230], "collect_ucl_fotmob"),
    ("KLEAGUE1", "KLEAGUE1_2026_27", [919356, 955135, 955147], "collect_kleague1_fotmob"),
    ("ALEAGUE", "ALEAGUE_2026_27", [1000001678], "collect_aleague_fotmob"),
    ("ACLELITE", "ACLELITE_2026_27", [943983, 943984], "collect_aclelite_fotmob"),
]

TABLES = {
    "players": "players", "substitutions": "subs", "events": "events",
    "match_meta": "meta", "shots": "shots", "assists": "assists",
}


def log(message: str) -> None:
    print(message, flush=True)


def get_json(url: str, net, retries: int = 3) -> dict | None:
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


def load_module(name: str):
    path = os.path.join(PIPELINE, f"{name}.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collected_ids(prefix: str) -> set[str]:
    """Provider match ids already stored, read from the batch JSON URLs."""
    ids = set()
    for name in os.listdir(SEASON_DIR):
        if not (name.startswith(prefix) and "player_detail_batch" in name and name.endswith(".json")):
            continue
        try:
            payload = json.load(open(os.path.join(SEASON_DIR, name), encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        url = payload.get("official_match_url") or ""
        found = re.search(r"/matches/(\d+)", url)
        if found:
            ids.add(found.group(1))
    return ids


def refresh_fixtures(prefix: str, league_ids: list[int], net, slack_days: int = 3) -> list[dict]:
    """Rescan a window around the stored fixtures and merge new entries."""
    path = os.path.join(SEASON_DIR, f"{prefix}_fixtures.json")
    fixtures = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    if not fixtures:
        return fixtures
    known = {f["id"] for f in fixtures}
    dates = [f["date"] for f in fixtures]
    start = datetime.strptime(max(dates), "%Y-%m-%d").date() - timedelta(days=slack_days)
    stop = date.today() + timedelta(days=2)
    added = 0
    day = start
    while day <= stop:
        payload = get_json(f"https://www.fotmob.com/api/data/matches?date={day.strftime('%Y%m%d')}", net)
        if payload:
            for league in payload.get("leagues") or []:
                if league.get("id") not in league_ids:
                    continue
                for match in league.get("matches") or []:
                    if match["id"] in known:
                        continue
                    known.add(match["id"])
                    status = ((match.get("status") or {}).get("reason") or {}).get("short") or ""
                    fixtures.append({
                        "date": day.isoformat(), "home": match["home"]["name"],
                        "away": match["away"]["name"], "status": status, "id": match["id"],
                        "homeId": match["home"]["id"], "awayId": match["away"]["id"],
                    })
                    added += 1
        day += timedelta(days=1)
    if added:
        fixtures.sort(key=lambda f: (f["date"], f["home"]))
        json.dump(fixtures, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"  fixtures: {len(fixtures)} known, {added} newly discovered")
    return fixtures


def append_rows(prefix: str, table: str, rows: list[dict]) -> None:
    path = os.path.join(SEASON_DIR, f"{prefix}_{table}.csv")
    if not rows:
        return
    existing, header = [], list(rows[0].keys())
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or header
            existing = list(reader)
    merged = existing + [{key: row.get(key, "") for key in header} for row in rows]
    merged.sort(key=lambda r: (r.get("Date", ""), r.get("HomeTeam", ""), r.get("AwayTeam", "")))
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\r\n",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)


def update_fotmob_league(code: str, prefix: str, league_ids: list[int], module_name: str,
                         net, dry_run: bool) -> dict:
    module = load_module(module_name)
    if not module or not hasattr(module, "build_match"):
        return {"league": code, "status": "collector unavailable"}
    fixtures = refresh_fixtures(prefix, league_ids, net)
    done = collected_ids(prefix)
    completed = [f for f in fixtures if f.get("status") in ("FT", "AET", "Pen")]
    new = [f for f in completed if str(f["id"]) not in done]
    log(f"  completed {len(completed)}, already stored {len(completed) - len(new)}, new {len(new)}")
    if dry_run or not new:
        return {"league": code, "new_fixtures": len(new), "written": 0}

    written = 0
    for fixture in new:
        detail = get_json(FOTMOB_MATCH.format(match_id=fixture["id"]), net)
        if not detail or "lineup" not in (detail.get("content") or {}):
            log(f"    skipped {fixture['date']} {fixture['home']}-{fixture['away']}: no lineup")
            continue
        built = module.build_match(fixture, detail)
        payload = built["payload"]
        for table, key in TABLES.items():
            append_rows(prefix, table, built.get(key) or [])
        name = (f"{prefix}_j{payload['matchday']}_player_detail_batch_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d')}_"
                f"{re.sub(r'[^a-z0-9]+', '_', module.norm(payload['home']))}_"
                f"{re.sub(r'[^a-z0-9]+', '_', module.norm(payload['away']))}.json")
        with open(os.path.join(SEASON_DIR, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        written += 1
        log(f"    added {fixture['date']} {fixture['home']}-{fixture['away']} {payload['score']}")
    return {"league": code, "new_fixtures": len(new), "written": written}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-import", action="store_true")
    args = parser.parse_args()
    net = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    summary = []
    for code, prefix, ids, module_name in FOTMOB_LEAGUES:
        log(f"[{code}]")
        try:
            summary.append(update_fotmob_league(code, prefix, ids, module_name, net, args.dry_run))
        except Exception as exc:                      # keep going for the other leagues
            log(f"  !! {code} failed: {type(exc).__name__}: {exc}")
            summary.append({"league": code, "status": f"error: {exc}"})

    # LaLiga keeps its own incremental collector: it skips fixtures that are stored.
    log("[LALIGA] running the official incremental collector")
    if not args.dry_run:
        result = subprocess.run([sys.executable,
                                 os.path.join(PIPELINE, "collect_laliga_official_player_detail.py"),
                                 "--all"], capture_output=True, text=True)
        tail = (result.stdout or "").strip().splitlines()[-3:]
        log("  " + " | ".join(tail) if tail else "  no output")
        summary.append({"league": "LALIGA", "status": "collector ran", "tail": tail})
    else:
        summary.append({"league": "LALIGA", "status": "dry run"})

    log("[J1] manual pass required: fixtures are keyed by an official match_card_id")
    summary.append({"league": "J1", "status": "manual: match_card_id lookup required"})

    if not args.dry_run and not args.skip_import:
        log("[SQLITE] refreshing the analysis layer")
        result = subprocess.run([sys.executable,
                                 os.path.join(PIPELINE, "import_datasets_to_sqlite.py")],
                                capture_output=True, text=True)
        tail = (result.stdout or "").strip().splitlines()[-2:]
        log("  " + " | ".join(tail) if tail else "  no output")

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "dry-run" if args.dry_run else "update",
        "leagues": summary,
    }
    path = os.path.join(SEASON_DIR, f"incremental_update_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json")
    json.dump(report, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(path, "a", encoding="utf-8").write("\n")
    total = sum(item.get("written", 0) for item in summary)
    log(f"done: {total} new fixtures written")
    log(f"report: {os.path.relpath(path, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
