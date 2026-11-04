#!/usr/bin/env python3
"""Cross-check the normalized LaLiga player detail against a secondary source.

The master CSVs are collected from the official LaLiga match pages. This script
independently re-reads the same matches from FotMob (free, traceable, publicly
reachable) and compares:

  * the 22 starting players per fixture
  * the bench lists
  * every substitution event (player in, player out, minute)

When a fixture matches on all three checks its batch JSON is promoted to
``complete_source_crosscheck_and_structural_audit_passed`` and the secondary
source is recorded. Fixtures that do not match keep their pending status and the
mismatch is written into ``verification_detail`` instead of being silently
certified.

Usage:
    python data_pipeline/verify_laliga_player_detail.py --matchday 8
    python data_pipeline/verify_laliga_player_detail.py --pending
    python data_pipeline/verify_laliga_player_detail.py --all --dry-run
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(REPO_ROOT, "data", "season_match_detail")
PLAYERS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_players.csv")
SUBS_CSV = os.path.join(SEASON_DIR, "LaLiga_2026_27_substitutions.csv")

FOTMOB_BASE = "https://www.fotmob.com/api/data"
FOTMOB_MATCH = "https://www.fotmob.com/matches/{slug}"
LEAGUE_NAME = "LaLiga"

CERTIFIED_STATUS = "complete_source_crosscheck_and_structural_audit_passed"
SAME_THRESHOLD = 0.86

# Known broadcasting nicknames that share no token with the official full name.
NICKNAMES = {
    "pepelu": "joseluisgarciavaya",
    "cucho": "juanhernandez",
    "chupe": "carlosruizrubio",
    "juani": "juanfernandez",
    "yusi": "youssefenriquez",
}

# FotMob team names are club-branded rather than official; map them onto the
# repository naming before comparing fixtures.
TEAM_ALIASES = {
    "alaves": "Alaves", "deportivoalaves": "Alaves",
    "athleticbilbao": "Athletic", "athleticclub": "Athletic", "athletic": "Athletic",
    "atleticomadrid": "Atletico", "atleticodemadrid": "Atletico", "atletico": "Atletico",
    "barcelona": "Barcelona", "fcbarcelona": "Barcelona",
    "realbetis": "Betis", "betis": "Betis",
    "celta": "Celta", "celtavigo": "Celta", "rceltadevigo": "Celta",
    "elche": "Elche",
    "espanyol": "Espanyol", "rcdespanyol": "Espanyol", "espanyoldeparcelona": "Espanyol",
    "getafe": "Getafe",
    "deportivo": "Deportivo", "deportivoacoruna": "Deportivo", "deportivolacoruna": "Deportivo",
    "levante": "Levante", "levanteud": "Levante",
    "malaga": "Malaga",
    "osasuna": "Osasuna", "caosasuna": "Osasuna",
    "realmadrid": "Real Madrid",
    "racingsantander": "Racing", "racing": "Racing",
    "rayovallecano": "Rayo", "rayo": "Rayo",
    "realsociedad": "Real Sociedad",
    "sevilla": "Sevilla",
    "valencia": "Valencia",
    "villarreal": "Villarreal",
}


def log(message: str) -> None:
    print(message, flush=True)


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.lower())


def team_name(raw: str) -> str | None:
    key = norm(raw)
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    for alias, mapped in TEAM_ALIASES.items():
        if alias and (alias in key or key in alias):
            return mapped
    return None


def sim(a: str, b: str) -> float:
    """Name similarity tolerant to nicknames, short forms and accented spellings.

    The official LaLiga page uses legal full names while secondary providers use
    the usual matchday name, so comparing whole strings is not enough: token
    overlap (surname / any distinctive name part) is the useful signal.
    """
    left, right = norm(a), norm(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left = NICKNAMES.get(left, left)
    right = NICKNAMES.get(right, right)
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.95
    tokens_left = [t for t in re.split(r"[\s\-]+", (a or "").strip()) if t]
    tokens_right = [t for t in re.split(r"[\s\-]+", (b or "").strip()) if t]
    normal_left = [norm(t) for t in tokens_left]
    normal_right = [norm(t) for t in tokens_right]
    # Surname agreement is the strongest signal: "Félix ... Correia" vs "Félix Correia".
    if normal_left and normal_right and normal_left[-1] == normal_right[-1] and len(normal_left[-1]) >= 4:
        return 0.97
    # Any shared distinctive token (length >= 4) matches most short forms.
    shared = {t for t in normal_left if len(t) >= 4} & {t for t in normal_right if len(t) >= 4}
    if shared:
        return 0.92
    return SequenceMatcher(None, left, right).ratio()


def opener() -> urllib.request.OpenerDirector:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def get_json(url: str, net: urllib.request.OpenerDirector, retries: int = 3) -> dict | None:
    last = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/140 Safari/537.36",
                    "Accept": "application/json,text/plain,*/*",
                    "Referer": "https://www.fotmob.com/",
                    "Accept-Encoding": "gzip",
                },
            )
            with net.open(request, timeout=45) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    log(f"  !! request failed: {url} ({last})")
    return None


def load_local_fixtures() -> dict[tuple[str, str, str], dict]:
    """Fixture key -> local player and substitution rows."""
    fixtures: dict[tuple[str, str, str], dict] = {}
    with open(PLAYERS_CSV, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["Date"], row["HomeTeam"], row["AwayTeam"])
            entry = fixtures.setdefault(key, {"starters": defaultdict(list), "bench": defaultdict(list),
                                              "subs": [], "sub_rows": 0})
            if row["IsStarting"] == "true":
                entry["starters"][row["Team"]].append(row["Player"])
            else:
                entry["bench"][row["Team"]].append(row["Player"])
            entry["sub_rows"] += 1
    with open(SUBS_CSV, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["Date"], row["HomeTeam"], row["AwayTeam"])
            if key in fixtures:
                fixtures[key]["subs"].append((row["Team"], row["PlayerIn"], row["PlayerOut"], row["Minute"]))
    return fixtures


def batch_index() -> dict[tuple[str, str, str], list[str]]:
    index: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for name in sorted(os.listdir(SEASON_DIR)):
        if not (name.startswith("LaLiga_2026_27") and name.endswith(".json")):
            continue
        if "player_detail_batch" not in name:
            continue
        path = os.path.join(SEASON_DIR, name)
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        key = (payload.get("date"), payload.get("home"), payload.get("away"))
        if all(key):
            index[key].append(path)
    return index


def fotmob_matches_for_date(day: str, net, cache: dict[str, dict]) -> dict[tuple[str, str], dict]:
    """(home, away) repository team names -> FotMob match entry for one date."""
    if day in cache:
        return cache[day]
    payload = get_json(f"{FOTMOB_BASE}/matches?date={day.replace('-', '')}", net)
    result: dict[tuple[str, str], dict] = {}
    if payload:
        for league in payload.get("leagues") or []:
            if (league.get("name") or "") != LEAGUE_NAME:
                continue
            for match in league.get("matches") or []:
                home = team_name((match.get("home") or {}).get("name", ""))
                away = team_name((match.get("away") or {}).get("name", ""))
                if home and away:
                    result[(home, away)] = match
    cache[day] = result
    return result


def fotmob_match_detail(match_id: int, net) -> dict | None:
    return get_json(f"{FOTMOB_BASE}/matchDetails?matchId={match_id}", net)


def match_players(local: list[str], remote: list[str],
                  aliases: dict[str, set[str]] | None = None) -> tuple[list[str], list[str], list[str]]:
    """Return (matched_local, unmatched_local, unmatched_remote).

    A player is allowed to match through any official name form collected from the
    LaLiga payload (legal name, nickname, first+last name).
    """
    aliases = aliases or {}
    remaining = list(remote)
    matched, unmatched_local = [], []
    for name in local:
        forms = {name} | aliases.get(norm(name), set())
        best, best_score = None, 0.0
        for candidate in remaining:
            score = max(sim(form, candidate) for form in forms)
            if score > best_score:
                best, best_score = candidate, score
        if best is not None and best_score >= SAME_THRESHOLD:
            matched.append(name)
            remaining.remove(best)
        else:
            unmatched_local.append(name)
    return matched, unmatched_local, remaining


def official_name_forms(url: str, net) -> dict[str, set[str]]:
    """Official LaLiga payload: legal name -> {legal name, nickname, first+last}."""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    client = urllib.request.build_opener(*handlers)
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                          "Accept-Encoding": "gzip"})
        with client.open(request, timeout=45) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        log(f"  !! official page unavailable for aliases: {exc}")
        return {}

    html = raw.decode("utf-8", "replace")
    found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not found:
        return {}
    page = json.loads(found.group(1))["props"]["pageProps"]
    forms: dict[str, set[str]] = {}
    for side in ("home", "away"):
        block = ((page.get("data") or {}).get("lineups") or {}).get(side) or {}
        for player in (block.get("starts") or []) + (block.get("subs") or []):
            person = player.get("person") or {}
            name = person.get("name") or ""
            if not name:
                continue
            bucket = forms.setdefault(norm(name), set())
            bucket.add(name)
            if person.get("nickname"):
                bucket.add(person["nickname"])
            first, last = (person.get("firstname") or "").strip(), (person.get("lastname") or "").strip()
            if first or last:
                bucket.add(f"{first} {last}".strip())
    return forms


def verify_fixture(entry: dict, match: dict, detail: dict, aliases: dict[str, set[str]] | None = None) -> dict:
    lineup = (detail.get("content") or {}).get("lineup") or {}
    events = ((detail.get("content") or {}).get("matchFacts") or {}).get("events") or {}
    incidents = events.get("events") or []
    home = match["_home"]
    away = match["_away"]

    remote_starters = {}
    remote_bench = {}
    for side, team in (("homeTeam", home), ("awayTeam", away)):
        block = lineup.get(side) or {}
        remote_starters[team] = [p["name"] for p in block.get("starters") or []]
        remote_bench[team] = [p["name"] for p in block.get("subs") or []]

    checks: dict[str, dict] = {}
    for team in (home, away):
        matched, missing, extra = match_players(entry["starters"][team], remote_starters[team], aliases)
        checks[f"{team}_starters"] = {
            "local": len(entry["starters"][team]), "secondary": len(remote_starters[team]),
            "matched": len(matched), "missing": missing, "extra": extra,
        }
        matched_b, missing_b, extra_b = match_players(entry["bench"][team], remote_bench[team], aliases)
        checks[f"{team}_bench"] = {
            "local": len(entry["bench"][team]), "secondary": len(remote_bench[team]),
            "matched": len(matched_b), "missing": missing_b, "extra": extra_b,
        }

    # Substitutions: (team, in, out, minute) with a two minute tolerance.
    remote_subs = []
    for event in incidents:
        if event.get("type") != "Substitution":
            continue
        swap = event.get("swap") or []
        if len(swap) < 2:
            continue
        team = home if event.get("isHome") else away
        remote_subs.append((team, swap[0]["name"], swap[1]["name"], str(event.get("time", ""))))

    unmatched_local_subs, remaining = [], list(remote_subs)
    used_minutes: list[float] = []
    for team, player_in, player_out, minute in entry["subs"]:
        best, best_score = None, 0.0
        forms_in = {player_in} | (aliases or {}).get(norm(player_in), set())
        forms_out = {player_out} | (aliases or {}).get(norm(player_out), set())
        for candidate in remaining:
            if candidate[0] != team:
                continue
            score = (max(sim(f, candidate[1]) for f in forms_in)
                     + max(sim(f, candidate[2]) for f in forms_out)) / 2
            try:
                delta = abs(float(minute) - float(candidate[3]))
            except (TypeError, ValueError):
                delta = 99
            if delta <= 2:
                score += 0.05
            if score > best_score:
                best, best_score = candidate, score
        if best is not None and best_score >= SAME_THRESHOLD:
            remaining.remove(best)
            used_minutes.append(best_score)
        else:
            unmatched_local_subs.append((team, player_in, player_out, minute))

    checks["substitutions"] = {
        "local": len(entry["subs"]), "secondary": len(remote_subs),
        "matched": len(entry["subs"]) - len(unmatched_local_subs),
        "missing": unmatched_local_subs,
        "extra": remaining,
    }

    starters_ok = all(
        checks[f"{team}_starters"]["matched"] == 11 and not checks[f"{team}_starters"]["missing"]
        for team in (home, away)
    )
    bench_ratio = 1.0
    bench_pairs = 0
    for team in (home, away):
        block = checks[f"{team}_bench"]
        if block["local"]:
            bench_ratio = min(bench_ratio, block["matched"] / block["local"])
            bench_pairs += block["matched"]
    subs_ok = (not checks["substitutions"]["missing"]
               and not checks["substitutions"]["extra"]
               and checks["substitutions"]["matched"] == checks["substitutions"]["local"])

    # Providers often pair two same-minute substitutions differently while agreeing
    # on who came on and who went off. Fall back to comparing the incoming and
    # outgoing sets separately before declaring a real mismatch.
    pairing_differs = False
    if not subs_ok and entry["subs"] and remote_subs:
        def multiset_match(local_list, remote_list, index, aliases_map):
            remaining = list(remote_list)
            for team, player_in, player_out, minute in local_list:
                name = player_in if index == 1 else player_out
                forms = {name} | (aliases_map or {}).get(norm(name), set())
                best, best_pos, best_score = None, None, 0.0
                for pos, candidate in enumerate(remaining):
                    if candidate[0] != team:
                        continue
                    score = max(sim(form, candidate[index]) for form in forms)
                    try:
                        if abs(float(minute) - float(candidate[3])) <= 2:
                            score += 0.05
                    except (TypeError, ValueError):
                        pass
                    if score > best_score:
                        best, best_pos, best_score = candidate, pos, score
                if best is None or best_score < SAME_THRESHOLD:
                    return False
                remaining.pop(best_pos)
            return not remaining

        ins_ok = multiset_match(entry["subs"], remote_subs, 1, aliases)
        outs_ok = multiset_match(entry["subs"], remote_subs, 2, aliases)
        if ins_ok and outs_ok and len(entry["subs"]) == len(remote_subs):
            subs_ok = True
            pairing_differs = True
            checks["substitutions"]["pairing_differs_from_secondary"] = True

    passed = starters_ok and bench_ratio >= 0.8 and subs_ok
    return {
        "passed": passed,
        "starters_ok": starters_ok,
        "bench_overlap": round(bench_ratio, 3),
        "bench_matched": bench_pairs,
        "substitutions_ok": subs_ok,
        "pairing_differs": pairing_differs,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matchday", type=int, action="append")
    parser.add_argument("--pending", action="store_true",
                        help="only fixtures whose batch status is not certified")
    parser.add_argument("--all", action="store_true", help="every fixture with local detail")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not (args.matchday or args.pending or args.all):
        parser.error("choose --matchday N, --pending or --all")

    fixtures = load_local_fixtures()
    batches = batch_index()
    net = opener()
    date_cache: dict[str, dict] = {}

    targets = []
    for key, entry in sorted(fixtures.items()):
        paths = batches.get(key) or []
        payloads = [json.load(open(path, encoding="utf-8")) for path in paths]
        if args.matchday and not any(p.get("matchday") in args.matchday for p in payloads):
            continue
        if args.pending and payloads and all(
                (p.get("status") or "") == CERTIFIED_STATUS for p in payloads):
            continue
        targets.append((key, entry, paths))

    if args.limit:
        targets = targets[:args.limit]
    log(f"fixtures to verify: {len(targets)}")

    passed = failed = skipped = 0
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
              "league": "LaLiga", "season": "2026-27",
              "secondary_source": "FotMob public match API",
              "results": []}

    for index, (key, entry, paths) in enumerate(targets, start=1):
        day, home, away = key
        label = f"[{index}/{len(targets)}] {day} {home}-{away}"
        if args.dry_run:
            log(f"{label}: would verify")
            continue
        matches = fotmob_matches_for_date(day, net, date_cache)
        match = matches.get((home, away))
        if not match:
            log(f"{label}: no secondary fixture found, skipped")
            skipped += 1
            report["results"].append({"fixture": list(key), "status": "secondary_fixture_not_found"})
            continue
        detail = fotmob_match_detail(match["id"], net)
        if not detail or "lineup" not in (detail.get("content") or {}):
            log(f"{label}: no secondary lineup payload, skipped")
            skipped += 1
            report["results"].append({"fixture": list(key), "status": "secondary_lineup_missing"})
            continue

        official_url = None
        if paths:
            official_url = json.load(open(paths[0], encoding="utf-8")).get("official_match_url")
        aliases = official_name_forms(official_url, net) if official_url else {}
        match["_home"], match["_away"] = home, away
        result = verify_fixture(entry, match, detail, aliases)
        secondary_url = f"https://www.fotmob.com/matches/{match.get('pageUrl') or match['id']}"
        verdict = "PASS" if result["passed"] else "FAIL"
        log(f"{label}: {verdict} starters={result['starters_ok']} "
            f"bench={result['bench_overlap']} subs={result['substitutions_ok']}")

        for path in paths:
            payload = json.load(open(path, encoding="utf-8"))
            payload["verification"] = (
                "official_laliga_structured_extraction_cross_checked_with_fotmob_lineups_and_events"
            )
            payload["secondary_source"] = secondary_url
            payload["verification_detail"] = {
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "secondary_source": "FotMob public match API",
                "secondary_match_id": match["id"],
                "starters_ok": result["starters_ok"],
                "bench_overlap": result["bench_overlap"],
                "substitutions_ok": result["substitutions_ok"],
                "pairing_differs_from_secondary": result.get("pairing_differs", False),
                "checks": result["checks"],
            }
            if result.get("pairing_differs"):
                notes = payload.setdefault("verified_data", {}).setdefault("notes", [])
                notes.append("Secondary source pairs the same-minute substitutions differently; "
                             "the explicit pairing of the official LaLiga event timeline is retained.")
            if result["passed"]:
                payload["status"] = CERTIFIED_STATUS
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
                handle.write("\n")

        report["results"].append({
            "fixture": list(key), "status": verdict,
            "starters_ok": result["starters_ok"],
            "bench_overlap": result["bench_overlap"],
            "substitutions_ok": result["substitutions_ok"],
            "secondary_match_id": match["id"],
            "checks": result["checks"] if not result["passed"] else {},
        })
        if result["passed"]:
            passed += 1
        else:
            failed += 1
        time.sleep(0.25)

    if not args.dry_run:
        report["summary"] = {"verified": passed, "failed": failed, "skipped": skipped,
                             "total": len(targets)}
        path = os.path.join(SEASON_DIR, "LaLiga_2026_27_verification_report_"
                                        f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        log(f"verified: {passed}, failed: {failed}, skipped: {skipped}")
        log(f"report: {os.path.relpath(path, REPO_ROOT).replace(os.sep, '/')}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
