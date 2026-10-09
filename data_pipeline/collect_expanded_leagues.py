#!/usr/bin/env python3
"""Collect five additional competitions into an isolated database and mapping layer.

This pipeline intentionally does not write to football_model_database.sqlite or
reuse the existing 500-match validation sample. ESPN's competition-specific
scoreboard is used as a free fixture/result feed; every source event receives a
stable namespaced ID and an explicit mapping row.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path("data/expanded_leagues")
DB_PATH = ROOT / "expanded_leagues.sqlite"
REPORT_PATH = ROOT / "ingestion_report.json"
AS_OF = date.today()
API_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Season definitions deliberately follow each competition's own calendar.
COMPETITIONS = [
    {"key": "eng_championship", "name": "English Championship", "slug": "eng.2",
     "seasons": [
         {"label": "2025-26", "start": "2025-08-01", "end": "2026-06-30"},
         {"label": "2026-27", "start": "2026-08-01", "end": AS_OF.isoformat()},
     ]},
    {"key": "uefa_champions_league", "name": "UEFA Champions League", "slug": "uefa.champions",
     "seasons": [
         {"label": "2025-26", "start": "2025-07-01", "end": "2026-06-30"},
         {"label": "2026-27", "start": "2026-07-01", "end": AS_OF.isoformat()},
     ]},
    {"key": "portugal_primeira_liga", "name": "Portuguese Primeira Liga", "slug": "por.1",
     "seasons": [
         {"label": "2025-26", "start": "2025-08-01", "end": "2026-06-30"},
         {"label": "2026-27", "start": "2026-08-01", "end": AS_OF.isoformat()},
     ]},
    {"key": "norway_eliteserien", "name": "Norwegian Eliteserien", "slug": "nor.1",
     "seasons": [
         {"label": "2025", "start": "2025-01-01", "end": "2025-12-31"},
         {"label": "2026", "start": "2026-01-01", "end": AS_OF.isoformat()},
     ]},
    {"key": "finland_veikkausliiga", "name": "Finnish Veikkausliiga", "slug": "fin.1",
     "seasons": [
         {"label": "2025", "start": "2025-01-01", "end": "2025-12-31"},
         {"label": "2026", "start": "2026-01-01", "end": AS_OF.isoformat()},
     ]},
]

def connect():
    ROOT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS expanded_matches (
      expanded_match_id TEXT PRIMARY KEY,
      competition_key TEXT NOT NULL,
      competition_name TEXT NOT NULL,
      provider_slug TEXT NOT NULL,
      season_label TEXT NOT NULL,
      provider_event_id TEXT NOT NULL,
      kickoff_utc TEXT NOT NULL,
      home_team_id TEXT,
      home_team TEXT NOT NULL,
      away_team_id TEXT,
      away_team TEXT NOT NULL,
      match_status TEXT NOT NULL,
      is_finished INTEGER NOT NULL,
      home_goals INTEGER,
      away_goals INTEGER,
      venue TEXT,
      source_url TEXT NOT NULL,
      ingested_at_utc TEXT NOT NULL,
      UNIQUE(competition_key, season_label, provider_event_id)
    );
    CREATE TABLE IF NOT EXISTS expanded_match_mapping (
      expanded_match_id TEXT PRIMARY KEY,
      competition_key TEXT NOT NULL,
      season_label TEXT NOT NULL,
      provider TEXT NOT NULL,
      provider_event_id TEXT NOT NULL,
      source_match_name TEXT,
      mapping_status TEXT NOT NULL,
      mapping_method TEXT NOT NULL,
      mapping_confidence REAL NOT NULL,
      mapped_at_utc TEXT NOT NULL,
      FOREIGN KEY(expanded_match_id) REFERENCES expanded_matches(expanded_match_id)
    );
    CREATE TABLE IF NOT EXISTS expanded_team_mapping (
      competition_key TEXT NOT NULL,
      season_label TEXT NOT NULL,
      provider TEXT NOT NULL,
      provider_team_id TEXT NOT NULL,
      provider_team_name TEXT NOT NULL,
      normalized_team_key TEXT NOT NULL,
      mapping_status TEXT NOT NULL,
      mapping_method TEXT NOT NULL,
      mapped_at_utc TEXT NOT NULL,
      PRIMARY KEY(competition_key, season_label, provider_team_id)
    );
    CREATE TABLE IF NOT EXISTS expanded_samples (
      sample_id TEXT PRIMARY KEY,
      expanded_match_id TEXT NOT NULL UNIQUE,
      competition_key TEXT NOT NULL,
      season_label TEXT NOT NULL,
      sample_split TEXT NOT NULL CHECK(sample_split IN ('train','calibration','holdout')),
      chronological_index INTEGER NOT NULL,
      split_method TEXT NOT NULL,
      actual_result TEXT NOT NULL CHECK(actual_result IN ('H','D','A')),
      goal_difference INTEGER NOT NULL,
      total_goals INTEGER NOT NULL,
      created_at_utc TEXT NOT NULL,
      FOREIGN KEY(expanded_match_id) REFERENCES expanded_matches(expanded_match_id)
    );
    CREATE TABLE IF NOT EXISTS expanded_ingestion_runs (
      run_id TEXT PRIMARY KEY,
      started_at_utc TEXT NOT NULL,
      finished_at_utc TEXT NOT NULL,
      as_of_date TEXT NOT NULL,
      status TEXT NOT NULL,
      competition_season_count INTEGER NOT NULL,
      event_count INTEGER NOT NULL,
      finished_match_count INTEGER NOT NULL,
      mapping_unresolved_count INTEGER NOT NULL,
      errors_json TEXT NOT NULL,
      counts_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_exp_matches_comp_season_kickoff
      ON expanded_matches(competition_key, season_label, kickoff_utc);
    CREATE INDEX IF NOT EXISTS idx_exp_samples_comp_season_split
      ON expanded_samples(competition_key, season_label, sample_split);
    """)
    con.commit()
    return con

def month_windows(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        yield max(cur, start), min(last, end)
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

def fetch_scoreboard(slug: str, start: date, end: date):
    query = urllib.parse.urlencode({
        "dates": f"{start:%Y%m%d}-{end:%Y%m%d}",
        "limit": "1000",
    })
    url = f"{API_BASE}/{slug}/scoreboard?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "football-model-independent-data-pipeline/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"source request failed {url}: {exc}") from exc
    events = payload.get("events") or []
    # ESPN may return the same event in adjacent month/range responses; caller deduplicates by ID.
    return events, url

def event_to_row(event, comp, season, source_url):
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        return None
    comps = event.get("competitions") or []
    if not comps:
        return None
    competitors = comps[0].get("competitors") or []
    home = next((x for x in competitors if x.get("homeAway") == "home"), None)
    away = next((x for x in competitors if x.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    home_team = (home.get("team") or {})
    away_team = (away.get("team") or {})
    home_name = str(home_team.get("displayName") or home_team.get("name") or "").strip()
    away_name = str(away_team.get("displayName") or away_team.get("name") or "").strip()
    if not home_name or not away_name:
        return None
    status_obj = ((event.get("status") or {}).get("type") or {})
    status_name = str(status_obj.get("name") or status_obj.get("state") or "unknown")
    finished = bool(status_obj.get("completed"))
    hg = ag = None
    try:
        if finished and home.get("score") is not None and away.get("score") is not None:
            hg, ag = int(float(home["score"])), int(float(away["score"]))
        else:
            finished = False
    except (TypeError, ValueError):
        finished = False
        hg = ag = None
    kickoff = str(event.get("date") or "")
    try:
        parsed = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        kickoff = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return None
    venue_obj = comps[0].get("venue") or event.get("competitions", [{}])[0].get("venue") or {}
    venue = venue_obj.get("fullName") or venue_obj.get("shortName")
    # Stable namespace prevents collisions with legacy matches.match_id.
    canonical = f"XL:{comp['key']}:{season['label']}:{event_id}"
    return {
        "expanded_match_id": canonical,
        "competition_key": comp["key"],
        "competition_name": comp["name"],
        "provider_slug": comp["slug"],
        "season_label": season["label"],
        "provider_event_id": event_id,
        "kickoff_utc": kickoff,
        "home_team_id": str(home_team.get("id") or ""),
        "home_team": home_name,
        "away_team_id": str(away_team.get("id") or ""),
        "away_team": away_name,
        "match_status": status_name,
        "is_finished": int(finished),
        "home_goals": hg,
        "away_goals": ag,
        "venue": venue,
        "source_url": source_url,
        "ingested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

def normalize_team_key(name: str) -> str:
    import re
    value = name.casefold().strip()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value

def upsert_match(con, row):
    cols = list(row.keys())
    sql = ("INSERT INTO expanded_matches (" + ",".join(cols) + ") VALUES (" +
           ",".join("?" for _ in cols) + ") ON CONFLICT(expanded_match_id) DO UPDATE SET " +
           ",".join(f"{c}=excluded.{c}" for c in cols if c != "expanded_match_id"))
    con.execute(sql, [row[c] for c in cols])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("""INSERT INTO expanded_match_mapping
      (expanded_match_id,competition_key,season_label,provider,provider_event_id,source_match_name,
       mapping_status,mapping_method,mapping_confidence,mapped_at_utc)
      VALUES (?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(expanded_match_id) DO UPDATE SET
       provider_event_id=excluded.provider_event_id,source_match_name=excluded.source_match_name,
       mapping_status=excluded.mapping_status,mapping_method=excluded.mapping_method,
       mapping_confidence=excluded.mapping_confidence,mapped_at_utc=excluded.mapped_at_utc""",
      (row["expanded_match_id"], row["competition_key"], row["season_label"], "espn",
       row["provider_event_id"], f'{row["home_team"]} vs {row["away_team"]}',
       "mapped_exact", "competition+season+provider_event_id", 1.0, now))
    for tid, name in ((row["home_team_id"], row["home_team"]), (row["away_team_id"], row["away_team"])):
        if not tid:
            continue
        con.execute("""INSERT INTO expanded_team_mapping
          (competition_key,season_label,provider,provider_team_id,provider_team_name,
           normalized_team_key,mapping_status,mapping_method,mapped_at_utc)
          VALUES (?,?,?,?,?,?,?,?,?)
          ON CONFLICT(competition_key,season_label,provider_team_id) DO UPDATE SET
           provider_team_name=excluded.provider_team_name,
           normalized_team_key=excluded.normalized_team_key,
           mapped_at_utc=excluded.mapped_at_utc""",
          (row["competition_key"], row["season_label"], "espn", tid, name,
           normalize_team_key(name), "mapped_exact", "provider_team_id", now))

def rebuild_samples(con):
    con.execute("DELETE FROM expanded_samples")
    groups = con.execute("""
      SELECT competition_key,season_label,expanded_match_id,kickoff_utc,home_goals,away_goals
      FROM expanded_matches
      WHERE is_finished=1 AND home_goals IS NOT NULL AND away_goals IS NOT NULL
      ORDER BY competition_key,season_label,kickoff_utc,expanded_match_id
    """).fetchall()
    by_group = {}
    for r in groups:
        by_group.setdefault((r[0], r[1]), []).append(r)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for (comp, season), rows in by_group.items():
        n = len(rows)
        # Deterministic chronological split, isolated within each competition-season.
        train_end = int(n * 0.60)
        cal_end = int(n * 0.80)
        if n >= 5:
            train_end = max(1, min(n - 2, train_end))
            cal_end = max(train_end + 1, min(n - 1, cal_end))
        for idx, r in enumerate(rows):
            split = "train" if idx < train_end else "calibration" if idx < cal_end else "holdout"
            hg, ag = int(r[4]), int(r[5])
            result = "H" if hg > ag else "D" if hg == ag else "A"
            sample_id = "XS:" + hashlib.sha256(r[2].encode()).hexdigest()[:24]
            con.execute("""INSERT INTO expanded_samples
              (sample_id,expanded_match_id,competition_key,season_label,sample_split,
               chronological_index,split_method,actual_result,goal_difference,total_goals,created_at_utc)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (sample_id, r[2], comp, season, split, idx + 1,
               "chronological_60_20_20_within_competition_season", result, hg - ag, hg + ag, now))

def write_csvs(con):
    exports = {
        "expanded_matches.csv": "SELECT * FROM expanded_matches ORDER BY competition_key,season_label,kickoff_utc",
        "expanded_match_mapping.csv": "SELECT * FROM expanded_match_mapping ORDER BY competition_key,season_label,provider_event_id",
        "expanded_team_mapping.csv": "SELECT * FROM expanded_team_mapping ORDER BY competition_key,season_label,provider_team_name",
        "expanded_samples.csv": "SELECT * FROM expanded_samples ORDER BY competition_key,season_label,chronological_index",
    }
    for filename, sql in exports.items():
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.execute(sql).description]
        with (ROOT / filename).open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            writer.writerows(rows)

def main():
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    con = connect()
    errors = []
    counts = {}
    seen = 0

    for comp in COMPETITIONS:
        for season in comp["seasons"]:
            start = date.fromisoformat(season["start"])
            end = date.fromisoformat(season["end"])
            key = f"{comp['key']}:{season['label']}"
            found = {}
            if start > AS_OF:
                errors.append(f"{key}: season start is after as-of date")
                continue
            for win_start, win_end in month_windows(start, min(end, AS_OF)):
                try:
                    events, source_url = fetch_scoreboard(comp["slug"], win_start, win_end)
                    for event in events:
                        row = event_to_row(event, comp, season, source_url)
                        if row:
                            found[row["provider_event_id"]] = row
                except Exception as exc:
                    errors.append(f"{key} {win_start}/{win_end}: {exc}")
                time.sleep(0.12)
            if not found:
                errors.append(f"{key}: zero valid events returned; do not mark season complete")
            for row in found.values():
                upsert_match(con, row)
            seen += len(found)
            counts[key] = {
                "events": len(found),
                "finished": sum(x["is_finished"] for x in found.values()),
                "upcoming_or_incomplete": sum(not x["is_finished"] for x in found.values()),
                "source_slug": comp["slug"],
                "date_start": season["start"],
                "date_end": min(end, AS_OF).isoformat(),
            }
            print(json.dumps({"competition_season": key, **counts[key]}, ensure_ascii=False))

    rebuild_samples(con)
    con.commit()
    unresolved = con.execute("""
      SELECT COUNT(*) FROM expanded_matches m
      LEFT JOIN expanded_match_mapping x ON x.expanded_match_id=m.expanded_match_id
      WHERE x.expanded_match_id IS NULL OR x.mapping_status!='mapped_exact'
    """).fetchone()[0]
    match_count = con.execute("SELECT COUNT(*) FROM expanded_matches").fetchone()[0]
    finished_count = con.execute("SELECT COUNT(*) FROM expanded_matches WHERE is_finished=1").fetchone()[0]
    sample_count = con.execute("SELECT COUNT(*) FROM expanded_samples").fetchone()[0]
    status = "COMPLETED" if not errors and match_count and unresolved == 0 else "PARTIAL_OR_FAILED"
    report = {
        "status": status,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_date": AS_OF.isoformat(),
        "source": "ESPN public competition-specific scoreboard API",
        "database": str(DB_PATH),
        "isolation": "separate database, namespaced IDs, separate mappings and samples; no writes to production DB",
        "season_split_policy": "chronological 60/20/20 independently within each competition-season; completed matches only",
        "competition_season_count": len(counts),
        "event_count": match_count,
        "finished_match_count": finished_count,
        "sample_count": sample_count,
        "mapping_rows": con.execute("SELECT COUNT(*) FROM expanded_match_mapping").fetchone()[0],
        "team_mapping_rows": con.execute("SELECT COUNT(*) FROM expanded_team_mapping").fetchone()[0],
        "mapping_unresolved_count": unresolved,
        "counts": counts,
        "errors": errors,
    }
    con.execute("""INSERT OR REPLACE INTO expanded_ingestion_runs
      (run_id,started_at_utc,finished_at_utc,as_of_date,status,competition_season_count,
       event_count,finished_match_count,mapping_unresolved_count,errors_json,counts_json)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
      (run_id, started, report["finished_at_utc"], AS_OF.isoformat(), status,
       len(counts), match_count, finished_count, unresolved,
       json.dumps(errors, ensure_ascii=False), json.dumps(counts, ensure_ascii=False)))
    con.commit()
    write_csvs(con)
    con.close()
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if status != "COMPLETED":
        raise SystemExit("EXPANDED_LEAGUE_INGESTION_NOT_COMPLETE")

if __name__ == "__main__":
    main()
