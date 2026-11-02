#!/usr/bin/env python3
"""Enrich J1 2026/27 official lineup rows with official J.League player IDs and per-match minutes.

Only public J.League roster/profile pages are used. Values are written only when a unique
profile ID matches the roster name/team and the profile's J1 participation history contains
the exact match date. Unresolved or ambiguous values remain blank.
"""
from __future__ import annotations
import csv, json, re, time, unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path("data/season_match_detail")
PLAYERS = ROOT / "j1_2026_players_free_official.csv"
MAPPING = ROOT / "j1_2026_match_id_mapping.csv"
AUDIT = ROOT / "j1_2026_player_id_minutes_enrichment_audit.json"
ROSTER_INDEX_URL = "https://www.jleague.jp/news/article/33139/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; J1OfficialPlayerAudit/1.0)"}
PROFILE_RE = re.compile(r"/(?:en/)?player/(\d+)/?")
DATE_RE = re.compile(r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})")

def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()

def name_key(value):
    value = unicodedata.normalize("NFKC", clean(value)).casefold()
    return re.sub(r"[\s\u3000・･'’.,，．()（）\-‐—_]+", "", value)

TEAM_NAME_GROUPS = {
    "鹿島": ["鹿島", "鹿島アントラーズ"],
    "水戸": ["水戸", "水戸ホーリーホック"],
    "浦和": ["浦和", "浦和レッズ"],
    "千葉": ["千葉", "ジェフユナイテッド千葉", "ジェフユナイテッド市原千葉"],
    "柏": ["柏", "柏レイソル"],
    "FC東京": ["FC東京", "東京FC"],
    "東京V": ["東京V", "東京ヴェルディ"],
    "町田": ["町田", "FC町田ゼルビア"],
    "川崎F": ["川崎F", "川崎フロンターレ"],
    "横浜FM": ["横浜FM", "横浜F・マリノス", "横浜Fマリノス"],
    "清水": ["清水", "清水エスパルス"],
    "名古屋": ["名古屋", "名古屋グランパス"],
    "京都": ["京都", "京都サンガF.C.", "京都サンガ"],
    "G大阪": ["G大阪", "ガンバ大阪"],
    "C大阪": ["C大阪", "セレッソ大阪"],
    "神戸": ["神戸", "ヴィッセル神戸"],
    "岡山": ["岡山", "ファジアーノ岡山"],
    "広島": ["広島", "サンフレッチェ広島"],
    "福岡": ["福岡", "アビスパ福岡"],
    "長崎": ["長崎", "V・ファーレン長崎"],
}
def team_key(value):
    key = name_key(value).replace("fc", "")
    for canonical, aliases in TEAM_NAME_GROUPS.items():
        if key in {name_key(alias).replace("fc", "") for alias in aliases}:
            return name_key(canonical).replace("fc", "")
    return key

def fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=35)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")

def official_roster_urls():
    soup = fetch(ROSTER_INDEX_URL)
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(ROSTER_INDEX_URL, a["href"])
        text = clean(a.get_text(" ", strip=True))
        if "/club/" in href and "/player" in href and href not in seen:
            seen.add(href)
            found.append(href)
    if not found:
        raise RuntimeError("Could not discover official club roster pages from the J.League player-directory announcement.")
    return found

def parse_roster(url):
    soup = fetch(url)
    h1 = soup.find("h1")
    team_name = clean(h1.get_text(" ", strip=True)) if h1 else ""
    profiles = {}
    for a in soup.find_all("a", href=True):
        m = PROFILE_RE.search(a["href"])
        if not m:
            continue
        pid = m.group(1)
        name = clean(a.get_text(" ", strip=True))
        if not name:
            continue
        key = name_key(name)
        if key:
            profiles[(key, pid)] = {
                "player_name_roster": name,
                "player_id": pid,
                "team_roster_name": team_name,
                "roster_url": url,
                "profile_url": f"https://www.jleague.jp/en/player/{pid}/",
            }
    return team_name, list(profiles.values())

def parse_participation(soup):
    found = []
    for table in soup.find_all("table"):
        text = clean(table.get_text(" ", strip=True))
        if "Minutes Played" not in text and "Minutes played" not in text:
            continue
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [clean(c.get_text(" ", strip=True)).casefold() for c in rows[0].find_all(["th", "td"])]
        def idx(*terms):
            for i, h in enumerate(headers):
                if any(term in h for term in terms):
                    return i
            return -1
        date_i, comp_i, mins_i = idx("date"), idx("comp"), idx("minutes played", "minutes")
        if date_i < 0 or comp_i < 0 or mins_i < 0:
            continue
        for tr in rows[1:]:
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if max(date_i, comp_i, mins_i) >= len(cells):
                continue
            dm = DATE_RE.search(cells[date_i])
            if not dm or cells[comp_i].strip().upper() != "J1":
                continue
            try:
                match_date = datetime.strptime(dm.group(1), "%d %b %Y").date().isoformat()
            except ValueError:
                continue
            mm = re.search(r"(\d+)\s*min", cells[mins_i], re.I)
            if mm:
                found.append({"match_date": match_date, "actual_minutes": int(mm.group(1)), "competition": cells[comp_i]})
    return found

def main():
    with PLAYERS.open(encoding="utf-8-sig", newline="") as f:
        player_rows = list(csv.DictReader(f))
    with MAPPING.open(encoding="utf-8-sig", newline="") as f:
        matches = list(csv.DictReader(f))
    match_by_id = {m["internal_match_id"]: m for m in matches}

    roster_urls = official_roster_urls()
    roster_profiles = []
    roster_errors = []
    for i, url in enumerate(roster_urls, 1):
        try:
            team, profiles = parse_roster(url)
            roster_profiles.extend(profiles)
        except Exception as exc:
            roster_errors.append({"url": url, "error": str(exc)[:300]})
        if i % 5 == 0:
            print(f"Parsed {i}/{len(roster_urls)} official club roster pages", flush=True)
        time.sleep(0.12)

    by_name = defaultdict(list)
    ids = {}
    for p in roster_profiles:
        by_name[p["player_name_roster"] and name_key(p["player_name_roster"])].append(p)
        ids[p["player_id"]] = p

    def fetch_profile(item):
        try:
            soup = fetch(item["profile_url"])
            participation = parse_participation(soup)
            return item["player_id"], participation, ""
        except Exception as exc:
            return item["player_id"], [], str(exc)[:300]

    participation_by_id = {}
    profile_errors = {}
    unique_profiles = list(ids.values())
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_profile, p) for p in unique_profiles]
        for n, future in enumerate(as_completed(futures), 1):
            pid, participation, error = future.result()
            participation_by_id[pid] = participation
            if error:
                profile_errors[pid] = error
            if n % 50 == 0:
                print(f"Fetched {n}/{len(unique_profiles)} official player profiles", flush=True)

    # Match each lineup/bench row to a unique official profile by normalized name and,
    # where available, the roster club label. Ambiguous name-only matches are rejected.
    for row in player_rows:
        match = match_by_id.get(row.get("internal_match_id", ""), {})
        key = name_key(row.get("player_name", ""))
        candidates = by_name.get(key, [])
        team = team_key(row.get("team_report_label", ""))
        same_team = [p for p in candidates if team and team_key(p.get("team_roster_name", "")) == team]
        selected = same_team if len(same_team) == 1 else (candidates if len(candidates) == 1 else [])
        if selected:
            p = selected[0]
            row["player_id"] = p["player_id"]
            row["player_id_status"] = "verified_official_roster_profile"
            row["player_profile_url"] = p["profile_url"]
            row["player_id_source_url"] = p["roster_url"]
            same_date = [x for x in participation_by_id.get(p["player_id"], []) if x["match_date"] == match.get("match_date")]
            if len(same_date) == 1:
                row["actual_minutes"] = str(same_date[0]["actual_minutes"])
                row["actual_minutes_status"] = "verified_official_profile_j1_participation_history"
                row["actual_minutes_source_url"] = p["profile_url"]
            else:
                row["actual_minutes"] = ""
                row["actual_minutes_status"] = "not_found_or_ambiguous_for_match_date"
                row["actual_minutes_source_url"] = p["profile_url"]
        else:
            row.setdefault("player_profile_url", "")
            row.setdefault("player_id_source_url", "")
            row.setdefault("actual_minutes_source_url", "")
            if not row.get("player_id"):
                row["player_id_status"] = "unmatched_or_ambiguous_official_profile"
            if not row.get("actual_minutes"):
                row["actual_minutes_status"] = "not_found_or_ambiguous_official_profile"

    fields = list(player_rows[0].keys()) if player_rows else []
    for extra in ("player_profile_url", "player_id_source_url", "actual_minutes_source_url"):
        if extra not in fields:
            fields.append(extra)
    with PLAYERS.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(player_rows)

    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_policy": "free official J.League roster and player profile pages only",
        "roster_index_url": ROSTER_INDEX_URL,
        "official_roster_pages_discovered": len(roster_urls),
        "official_roster_pages_failed": roster_errors,
        "unique_official_player_profiles": len(unique_profiles),
        "player_profile_fetch_errors": len(profile_errors),
        "lineup_and_bench_rows": len(player_rows),
        "rows_with_official_player_id": sum(bool(r.get("player_id")) for r in player_rows),
        "rows_without_official_player_id": sum(not bool(r.get("player_id")) for r in player_rows),
        "rows_with_verified_match_minutes": sum(r.get("actual_minutes_status") == "verified_official_profile_j1_participation_history" for r in player_rows),
        "rows_without_verified_match_minutes": sum(r.get("actual_minutes_status") != "verified_official_profile_j1_participation_history" for r in player_rows),
        "minutes_method": "Read from the official player's J1 participation-history row for the exact match date; no minutes inferred from substitution events.",
        "id_method": "Match normalized official match-report player name to the current official club roster and unique official profile ID; ambiguous matches remain blank.",
        "profile_fetch_errors_sample": dict(list(profile_errors.items())[:20]),
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if roster_errors:
        raise SystemExit("Some official roster pages failed; see audit before accepting completeness.")

if __name__ == "__main__":
    main()
