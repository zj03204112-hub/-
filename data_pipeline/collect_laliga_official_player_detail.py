#!/usr/bin/env python3
"""Collect official LaLiga player detail (starting XIs, full benches, appearance
status and substitution events) into the repository normalized tables.

Data source: the official match pages on laliga.com. Every emitted row keeps the
official match URL in its ``Source`` column and every match gets a batch JSON
under ``data/season_match_detail`` using the same schema as the batches that were
produced manually for jornada 9-11.

Policy mirrors the collecting rules already documented in the repository:
  * free and traceable sources only
  * no inferred or guessed names
  * unresolved fields stay blank
  * postponed fixtures are never treated as completed

Usage:
    python data_pipeline/collect_laliga_official_player_detail.py --matchday 8 --dry-run
    python data_pipeline/collect_laliga_official_player_detail.py --matchday 8
    python data_pipeline/collect_laliga_official_player_detail.py --all
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
RESULTS_CSV = os.path.join(REPO_ROOT, "data", "auto_results", "LaLiga_2026_27.csv")
PLAYERS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_players.csv")
SUBS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_substitutions.csv")

LEAGUE = "LaLiga"
SEASON = "2026-27"
SEASON_SLUG = "2026-2027"
RESULTS_URL = "https://www.laliga.com/en-GB/laliga-easports/results/2026-27/gameweek-{gw}"
MATCH_URL = "https://www.laliga.com/en-GB/match/{slug}"

PLAYERS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Team", "Player", "IsStarting",
                  "OnBench", "AppearanceStatus", "EntryMinute", "ExitMinute", "Source"]
SUBS_HEADER = ["Date", "HomeTeam", "AwayTeam", "Team", "PlayerIn", "PlayerOut", "Minute", "Source"]

# Official laliga.com team slug -> normalized team name used by the repository CSVs.
TEAM_BY_SLUG = {
    "athletic-club": "Athletic",
    "atletico-de-madrid": "Atletico",
    "c-a-osasuna": "Osasuna",
    "d-alaves": "Alaves",
    "elche-c-f": "Elche",
    "fc-barcelona": "Barcelona",
    "getafe-cf": "Getafe",
    "levante-ud": "Levante",
    "malaga-cf": "Malaga",
    "r-racing-club": "Racing",
    "rayo-vallecano": "Rayo",
    "rc-celta": "Celta",
    "rc-deportivo": "Deportivo",
    "rcd-espanyol": "Espanyol",
    "real-betis": "Betis",
    "real-madrid": "Real Madrid",
    "real-sociedad": "Real Sociedad",
    "sevilla-fc": "Sevilla",
    "valencia-cf": "Valencia",
    "villarreal-cf": "Villarreal",
}

# football-data.co.uk team name -> repository team name (used for halftime scores only).
FDCO_TEAM = {
    "Alaves": "Alaves", "Ath Bilbao": "Athletic", "Ath Madrid": "Atletico",
    "Barcelona": "Barcelona", "Betis": "Betis", "Celta": "Celta", "Elche": "Elche",
    "Espanol": "Espanyol", "Getafe": "Getafe", "La Coruna": "Deportivo",
    "Levante": "Levante", "Malaga": "Malaga", "Osasuna": "Osasuna",
    "Real Madrid": "Real Madrid", "Santander": "Racing", "Sevilla": "Sevilla",
    "Sociedad": "Real Sociedad", "Valencia": "Valencia", "Vallecano": "Rayo",
    "Villarreal": "Villarreal",
}

STATUS_VERIFICATION = (
    "official_laliga_match_page_structured_extraction;"
    "secondary_source_crosscheck_pending"
)
BATCH_STATUS = "official_structured_extraction_passed_secondary_crosscheck_pending"


def log(msg: str) -> None:
    print(msg, flush=True)


def norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.lower())


def proxy_opener() -> urllib.request.OpenerDirector:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def fetch_next_data(url: str, opener: urllib.request.OpenerDirector,
                    retries: int = 3, timeout: int = 45) -> dict | None:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Accept-Encoding": "gzip",
                },
            )
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            html = raw.decode("utf-8", "replace")
            found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
            if not found:
                return None
            return json.loads(found.group(1))["props"]["pageProps"]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    log(f"  !! fetch failed: {url} ({last_error})")
    return None


def load_results_reference() -> dict[tuple[str, str, str], dict]:
    """Half-time scores from the football-data.co.uk export already in the repo."""
    reference = {}
    if not os.path.exists(RESULTS_CSV):
        return reference
    with open(RESULTS_CSV, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                day, month, year = row["Date"].split("/")
                iso = f"{year}-{month}-{day}"
                home = FDCO_TEAM.get(row["HomeTeam"], row["HomeTeam"])
                away = FDCO_TEAM.get(row["AwayTeam"], row["AwayTeam"])
                reference[(iso, home, away)] = {
                    "FTHG": row.get("FTHG", ""), "FTAG": row.get("FTAG", ""),
                    "HTHG": row.get("HTHG", ""), "HTAG": row.get("HTAG", ""),
                }
            except (ValueError, KeyError):
                continue
    return reference


def load_covered_fixtures() -> set[tuple[str, str, str]]:
    covered = set()
    if not os.path.exists(PLAYERS_CSV):
        return covered
    with open(PLAYERS_CSV, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            covered.add((row["Date"], row["HomeTeam"], row["AwayTeam"]))
    return covered


def serialize_rows(header: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, lineterminator="\r\n")
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in header})
    return buffer.getvalue()


def merge_csv_rows(path: str, header: list[str], new_rows: list[dict],
                   replace_keys: set[tuple[str, str, str]]) -> None:
    """Append fixture rows while leaving every other line of the file untouched.

    The repository master CSVs are append-ordered rather than date-sorted, so rows
    are never re-sorted here: existing lines are copied back byte for byte and only
    rows of fixtures that are being re-collected are dropped first (idempotency).
    """
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as handle:
            lines = handle.readlines()
    if not lines:
        lines = [",".join(header) + "\r\n"]

    kept: list[str] = []
    for line in lines:
        fields = line.split(",", 3)
        if len(fields) >= 3 and (fields[0], fields[1], fields[2]) in replace_keys:
            continue
        kept.append(line if line.endswith("\n") else line + "\r\n")

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(kept))
        handle.write(serialize_rows(header, new_rows))


def fetch_gameweek(gw: int, opener) -> list[dict]:
    payload = fetch_next_data(RESULTS_URL.format(gw=gw), opener)
    if not payload:
        return []
    for match in payload.get("matches", []):
        match["gameweek"] = gw
    return payload.get("matches", [])


def substitution_events(page: dict, team_id: int) -> list[dict]:
    events = []
    for event in page.get("events") or []:
        if (event.get("match_event_kind") or {}).get("collection") != "substitution":
            continue
        incoming = (event.get("lineup") or {}).get("team") or {}
        if incoming.get("id") != team_id:
            continue
        outgoing = (event.get("lineup_off") or {}).get("person") or {}
        events.append({
            "in": ((event.get("lineup") or {}).get("person") or {}).get("name", ""),
            "out": outgoing.get("name", ""),
            "minute": "" if event.get("time") is None else str(event.get("time")),
        })
    return events


def load_known_player_names() -> dict[tuple[str, str], str]:
    """Existing display name per (team, normalized name), used to flag variants."""
    known: dict[tuple[str, str], str] = {}
    for path in (PLAYERS_CSV, SUBS_CSV):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                for column in ("Player", "PlayerIn", "PlayerOut"):
                    name = row.get(column) or ""
                    if name:
                        known.setdefault((row.get("Team", ""), norm_name(name)), name)
    return known


def build_match_payload(match: dict, page: dict, reference: dict,
                        known_names: dict[tuple[str, str], str] | None = None) -> dict:
    home_slug = match["home_team"]["slug"]
    away_slug = match["away_team"]["slug"]
    home = TEAM_BY_SLUG[home_slug]
    away = TEAM_BY_SLUG[away_slug]
    iso_date = match["date"][:10]
    url = MATCH_URL.format(slug=match["slug"])

    lineups = page["data"]["lineups"]
    player_rows: list[dict] = []
    sub_rows: list[dict] = []
    notes: list[str] = []
    known = known_names or {}
    variants: dict[str, str] = {}

    def official_name(team_name: str, name: str) -> str:
        """Keep the official spelling but remember the repository variant."""
        existing = known.get((team_name, norm_name(name)))
        if existing and existing != name:
            variants[name] = existing
        return name
    verified_data = {
        "home_starting_xi": [], "home_bench": [], "away_starting_xi": [], "away_bench": [],
        "substitution_events": [], "notes": notes,
    }

    for side, team_name in (("home", home), ("away", away)):
        team_id = match[f"{side}_team"]["id"]
        side_lineup = lineups[side]
        events = substitution_events(page, team_id)
        exits: dict[str, str] = {}
        entries: dict[str, str] = {}
        for event in events:
            if event["out"]:
                exits.setdefault(norm_name(event["out"]), event["minute"])
            if event["in"]:
                entries.setdefault(norm_name(event["in"]), event["minute"])
            sub_rows.append({
                "Date": iso_date, "HomeTeam": home, "AwayTeam": away, "Team": team_name,
                "PlayerIn": official_name(team_name, event["in"]),
                "PlayerOut": official_name(team_name, event["out"]),
                "Minute": event["minute"], "Source": url,
            })
            verified_data["substitution_events"].append({
                "team": team_name,
                "player_in": official_name(team_name, event["in"]),
                "player_out": official_name(team_name, event["out"]),
                "minute": event["minute"], "source": url,
            })

        starters = [p for p in side_lineup.get("starts") or []]
        bench = [p for p in side_lineup.get("subs") or []]
        verified_data[f"{side}_starting_xi"] = [p["person"]["name"] for p in starters]
        verified_data[f"{side}_bench"] = [p["person"]["name"] for p in bench]

        for player in starters:
            name = official_name(team_name, player["person"]["name"])
            player_rows.append({
                "Date": iso_date, "HomeTeam": home, "AwayTeam": away, "Team": team_name,
                "Player": name, "IsStarting": "true", "OnBench": "false",
                "AppearanceStatus": "started",
                "EntryMinute": "",
                "ExitMinute": exits.get(norm_name(name), ""),
                "Source": url,
            })

        for player in bench:
            name = official_name(team_name, player["person"]["name"])
            minute = entries.get(norm_name(name), "")
            player_rows.append({
                "Date": iso_date, "HomeTeam": home, "AwayTeam": away, "Team": team_name,
                "Player": name, "IsStarting": "false", "OnBench": "true",
                "AppearanceStatus": "substitute_appeared" if minute else "substitute_not_appeared",
                "EntryMinute": minute, "ExitMinute": "", "Source": url,
            })

        unknown_in = [e["in"] for e in events if norm_name(e["in"]) not in
                      {norm_name(p["person"]["name"]) for p in bench}]
        for name in unknown_in:
            notes.append(f"{team_name}: substitution entrant '{name}' is not present in the "
                         f"official bench list; row kept from the event feed only.")

    fifa = reference.get((iso_date, home, away), {})
    home_score = match.get("home_score")
    away_score = match.get("away_score")
    halftime = ""
    if fifa.get("HTHG") not in (None, "") and fifa.get("HTAG") not in (None, ""):
        halftime = f"{fifa['HTHG']}-{fifa['HTAG']}"

    notes.append("Starting XIs, bench lists and appearance status extracted from the "
                 "structured match payload of the official LaLiga match page.")
    notes.append("Substitution minutes use the official event timeline 'time' value; "
                 "stoppage-time display clocks are not used as the stored minute.")
    notes.append("Secondary lineup cross-check (club site / AS / Sky) is still pending for "
                 "this match; do not treat it as fully certified.")
    if variants:
        notes.append("Official source spelling kept; the master CSV already stored a different "
                     "spelling for: " +
                     "; ".join(f"{a} (CSV: {b})" for a, b in sorted(variants.items())) + ".")

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "league": LEAGUE,
        "season": SEASON,
        "matchday": match["gameweek"],
        "match_id": f"laliga-2026-27-{iso_date}-{home_slug}-{away_slug}",
        "date": iso_date,
        "home": home,
        "away": away,
        "score": f"{home_score}-{away_score}",
        "halftime_score": halftime,
        "official_match_url": url,
        "verification": STATUS_VERIFICATION,
        "sources": {"official_laliga": url},
        "verified_data": verified_data,
        "normalized_csv": {
            "players": "data/season_match_detail/LaLiga_2026_27_players.csv",
            "substitutions": "data/season_match_detail/LaLiga_2026_27_substitutions.csv",
        },
        "status": BATCH_STATUS,
        "_player_rows": player_rows,
        "_sub_rows": sub_rows,
    }


def batch_filename(payload: dict) -> str:
    home = re.sub(r"[^a-z0-9]+", "_", norm_name(payload["home"]))
    away = re.sub(r"[^a-z0-9]+", "_", norm_name(payload["away"]))
    return (f"LaLiga_2026_27_j{payload['matchday']}_player_detail_batch_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d')}_{home}_{away}.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matchday", type=int, action="append",
                        help="only this matchday (repeatable)")
    parser.add_argument("--all", action="store_true", help="every matchday with results")
    parser.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    parser.add_argument("--force", action="store_true",
                        help="re-collect fixtures that already have player rows")
    parser.add_argument("--limit", type=int, default=0, help="stop after N fixtures")
    args = parser.parse_args()

    if not args.all and not args.matchday:
        parser.error("choose --matchday N or --all")

    matchdays = args.matchday or list(range(1, 12))
    opener = proxy_opener()
    reference = load_results_reference()
    known_names = load_known_player_names()
    covered = set() if args.force else load_covered_fixtures()
    log(f"existing fixtures with player rows: {len(covered)}")

    planned: list[dict] = []
    for matchday in matchdays:
        matches = fetch_gameweek(matchday, opener)
        if not matches:
            log(f"GW{matchday}: no fixtures found")
            continue
        pending = [m for m in matches if m.get("status") == "FullTime"]
        postponed = [m for m in matches if m.get("status") != "FullTime"]
        log(f"GW{matchday}: {len(matches)} fixtures, {len(pending)} completed, "
            f"{len(postponed)} not completed")
        for match in pending:
            home = TEAM_BY_SLUG[match["home_team"]["slug"]]
            away = TEAM_BY_SLUG[match["away_team"]["slug"]]
            key = (match["date"][:10], home, away)
            if key in covered:
                continue
            planned.append(match)
        for match in postponed:
            log(f"  skipped ({match.get('status')}): {match['home_team']['nickname']} - "
                f"{match['away_team']['nickname']}")

    if args.limit:
        planned = planned[:args.limit]
    log(f"fixtures to collect: {len(planned)}")
    if args.dry_run:
        for match in planned:
            log(f"  would collect GW{match['gameweek']}: {match['date'][:10]} "
                f"{TEAM_BY_SLUG[match['home_team']['slug']]} - "
                f"{TEAM_BY_SLUG[match['away_team']['slug']]}")
        return 0

    new_player_rows: list[dict] = []
    new_sub_rows: list[dict] = []
    collected_keys: set[tuple[str, str, str]] = set()
    written = 0
    failed = 0

    for index, match in enumerate(planned, start=1):
        url = MATCH_URL.format(slug=match["slug"])
        label = (f"GW{match['gameweek']} {match['date'][:10]} "
                 f"{TEAM_BY_SLUG[match['home_team']['slug']]} - "
                 f"{TEAM_BY_SLUG[match['away_team']['slug']]}")
        log(f"[{index}/{len(planned)}] {label}")
        page = fetch_next_data(url, opener)
        if not page or "lineups" not in (page.get("data") or {}):
            log("  !! no lineup payload, skipped")
            failed += 1
            continue
        payload = build_match_payload(match, page, reference, known_names)
        key = (payload["date"], payload["home"], payload["away"])
        new_player_rows.extend(payload.pop("_player_rows"))
        new_sub_rows.extend(payload.pop("_sub_rows"))
        collected_keys.add(key)

        target = os.path.join(SEASON_DIR, batch_filename(payload))
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        written += 1
        log(f"  starters {len(payload['verified_data']['home_starting_xi'])}/"
            f"{len(payload['verified_data']['away_starting_xi'])}, "
            f"bench {len(payload['verified_data']['home_bench'])}/"
            f"{len(payload['verified_data']['away_bench'])}, "
            f"subs {len(payload['verified_data']['substitution_events'])}")

    merge_csv_rows(PLAYERS_CSV, PLAYERS_HEADER, new_player_rows, collected_keys)
    merge_csv_rows(SUBS_CSV, SUBS_HEADER, new_sub_rows, collected_keys)
    log(f"batches written: {written}, failed: {failed}")
    log(f"rows appended: players {len(new_player_rows)}, substitutions {len(new_sub_rows)}")
    log(f"fixtures touched: {len(collected_keys)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
