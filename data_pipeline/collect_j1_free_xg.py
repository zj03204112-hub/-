#!/usr/bin/env python3
"""Collect publicly visible J1 match xG from OddAlerts without paid APIs.

Only values explicitly published in the public recent-results section are recorded.
The source's xG model is provider-specific and is not represented as official J.League xG.
Uncovered historical fixtures remain blank; no goals-based imputation is allowed.
"""
from __future__ import annotations
import csv, json, re
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path("data/season_match_detail")
MAPPING = ROOT / "j1_2026_match_id_mapping.csv"
OUT = ROOT / "j1_2026_match_xg_free.csv"
AUDIT = ROOT / "j1_2026_match_xg_free_audit.json"
TEAM_OUT = ROOT / "j1_2026_team_season_xg_free.csv"
SOURCE_URL = "https://www.oddalerts.com/xg/j-league"
TEAM_SOURCE_URL = "https://www.oddalerts.com/xg/j-league"
ALIASES = {
    "横浜fm": ["yokohama f. marinos", "yokohama marinos"],
    "鹿島": ["kashima antlers", "kashima"],
    "g大阪": ["gamba osaka"],
    "浦和": ["urawa reds", "urawa red diamonds", "urawa"],
    "c大阪": ["cerezo osaka"],
    "冈山": ["fagiano okayama"],
    "岡山": ["fagiano okayama"],
    "名古屋": ["nagoya grampus"],
    "清水": ["shimizu s-pulse", "shimizu s pulse"],
    "福岡": ["avispa fukuoka"],
    "神戸": ["vissel kobe"],
    "柏": ["kashiwa reysol"],
    "水戸": ["mito hollyhock"],
    "fc東京": ["fc tokyo", "tokyo"],
    "町田": ["machida zelvia"],
    "広島": ["sanfrecce hiroshima"],
    "千葉": ["jef united chiba", "jef united ichihara chiba", "jef united"],
    "東京v": ["tokyo verdy"],
    "川崎f": ["kawasaki frontale"],
    "長崎": ["v-varen nagasaki", "v varen nagasaki"],
    "京都": ["kyoto sanga", "kyoto sanga fc"],
}
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def norm(s: str) -> str:
    s = (s or "").lower().replace("Ｆ", "f").replace("Ｖ", "v").replace("Ｇ", "g").replace("Ｃ", "c")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()

def team_aliases(name: str):
    # Preserve Japanese characters when matching source keys; normalize full-width Latin letters.
    key = (name or "").replace("Ｆ", "F").replace("Ｖ", "V").replace("Ｇ", "G").replace("Ｃ", "C").casefold().replace(" ", "")
    for k, vals in ALIASES.items():
        kk = k.replace("Ｆ", "F").replace("Ｖ", "V").replace("Ｇ", "G").replace("Ｃ", "C").casefold().replace(" ", "")
        if kk == key:
            return vals
    return [name.lower()]

def contains_team(text: str, aliases: list[str]) -> bool:
    hay = " " + norm(text) + " "
    for alias in aliases:
        a = norm(alias)
        if not a:
            continue
        if (" " + a + " ") in hay:
            # Prevent bare "Tokyo" from accidentally matching Tokyo Verdy.
            if a == "tokyo" and " tokyo verdy " in hay and " fc tokyo " not in hay:
                continue
            return True
    return False

def load_mapping():
    with MAPPING.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def extract_candidates(soup):
    # Parse only the public "Recent Results with xG" panel. Match on date, home/away
    # teams, and final score so repeat fixtures cannot be assigned to the wrong round.
    lines = [clean(x) for x in soup.get_text("\n", strip=True).splitlines()]
    lines = [x for x in lines if x]
    start = next((i for i, x in enumerate(lines) if x.casefold() == "recent results with xg"), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].casefold().startswith("download this data as csv")
                or lines[i].casefold().startswith("j-league xg table")), len(lines))
    region = lines[start:end]
    months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
    current_date = ""
    out = []
    seen = set()
    for i, line in enumerate(region):
        dm = re.match(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+([A-Za-z]{3})\s+(\d{1,2})$", line, re.I)
        if dm:
            month = months.get(dm.group(1).lower())
            day = int(dm.group(2))
            if month:
                year = 2026 if month >= 7 else 2027
                current_date = f"{year:04d}-{month:02d}-{day:02d}"
            continue
        score = re.fullmatch(r"(\d{1,2})\s*[-–]\s*(\d{1,2})", line)
        if not score or not current_date or i < 2 or i + 2 >= len(region):
            continue
        try:
            home_xg = float(region[i - 2])
            away_xg = float(region[i + 2])
        except ValueError:
            continue
        home_name, away_name = region[i - 1], region[i + 1]
        home_goals, away_goals = int(score.group(1)), int(score.group(2))
        pair = None
        for match in MATCHES:
            if match.get("match_date", "") != current_date:
                continue
            if int(float(match.get("home_goals", "-1"))) != home_goals:
                continue
            if int(float(match.get("away_goals", "-1"))) != away_goals:
                continue
            if contains_team(home_name, team_aliases(match.get("home_team_source", ""))) and contains_team(away_name, team_aliases(match.get("away_team_source", ""))):
                pair = match
                break
        if pair is None:
            continue
        mid = pair["internal_match_id"]
        if mid in seen:
            continue
        raw = f"{current_date} | {home_xg:.3f} {home_name} {home_goals}-{away_goals} {away_name} {away_xg:.3f}"
        out.append({"match": pair, "home_xg": home_xg, "away_xg": away_xg, "raw_text": raw})
        seen.add(mid)
    return out

def main():
    global MATCHES
    matches = load_mapping()
    MATCHES = matches
    now = datetime.now(timezone.utc).isoformat()
    prior = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig", newline="") as f:
            prior = {r.get("internal_match_id", ""): r for r in csv.DictReader(f)}
    source_rows = []
    source_access_status = "skipped_existing_complete_data"
    source_access_detail = ""
    prior_complete = all(
        prior.get(m.get("internal_match_id", ""), {}).get("home_xg")
        and prior.get(m.get("internal_match_id", ""), {}).get("away_xg")
        and prior.get(m.get("internal_match_id", ""), {}).get("source_validation_status") == "verified_from_current_fetch"
        and prior.get(m.get("internal_match_id", ""), {}).get("source_name") == "OddAlerts"
        for m in matches
    )
    if prior_complete:
        print("All mapped fixtures already have sourced xG values; preserving provider provenance and skipping alternate-provider fetch.", flush=True)
    else:
        try:
            response = requests.get(SOURCE_URL, timeout=45, headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            source_rows = extract_candidates(soup)
            source_access_status = "fetched"
        except requests.RequestException as exc:
            source_access_status = "blocked_or_unavailable"
            source_access_detail = str(exc)[:500]
            print(f"Public xG source unavailable; preserving prior sourced values: {source_access_detail}", flush=True)
    by_id = {r["match"]["internal_match_id"]: r for r in source_rows}
    output = []
    for m in matches:
        mid = m["internal_match_id"]
        found = by_id.get(mid)
        old = prior.get(mid, {})
        if found:
            hxg, axg = found["home_xg"], found["away_xg"]
            row = {
                "internal_match_id": mid, "provider_event_id": m.get("provider_event_id",""),
                "match_date": m.get("match_date",""), "home_team": m.get("home_team_source",""),
                "away_team": m.get("away_team_source",""), "home_goals": m.get("home_goals",""),
                "away_goals": m.get("away_goals",""), "home_xg": f"{hxg:.3f}", "away_xg": f"{axg:.3f}",
                "home_xga": f"{axg:.3f}", "away_xga": f"{hxg:.3f}",
                "xg_status": "published_on_free_public_source", "source_name": "OddAlerts",
                "source_url": SOURCE_URL, "source_model_note": "Provider-specific xG from the public recent-results panel; not official J.League xG",
                "source_validation_status": "verified_from_current_fetch",
                "source_reverification_note": "Matched from the current successful free public page fetch.",
                "source_raw_row": found["raw_text"], "retrieved_at_utc": now,
            }
        elif old.get("home_xg") and old.get("away_xg"):
            # Preserve previously sourced and audited values if the public page blocks automation.
            row = dict(old)
            row.update({"internal_match_id": mid, "provider_event_id": m.get("provider_event_id",""),
                "match_date": m.get("match_date",""), "home_team": m.get("home_team_source",""),
                "away_team": m.get("away_team_source",""), "home_goals": m.get("home_goals",""),
                "away_goals": m.get("away_goals","")})
            if source_access_status != "fetched" or old.get("source_name") != "OddAlerts":
                row["xg_status"] = "unverified_legacy_value"
                row["source_validation_status"] = "unverified_legacy_value"
                row["source_reverification_note"] = "Value retained for audit only; current source fetch did not re-verify this row. Exclude from model calibration until reverified."
            else:
                row["source_validation_status"] = old.get("source_validation_status", "pending_source_reverification")
                row["source_reverification_note"] = old.get("source_reverification_note", "No row-level refresh was available in this run.")
        else:
            row = {
                "internal_match_id": mid, "provider_event_id": m.get("provider_event_id",""),
                "match_date": m.get("match_date",""), "home_team": m.get("home_team_source",""),
                "away_team": m.get("away_team_source",""), "home_goals": m.get("home_goals",""),
                "away_goals": m.get("away_goals",""), "home_xg": "", "away_xg": "",
                "home_xga": "", "away_xga": "", "xg_status": "missing_not_exposed_in_free_source",
                "source_name": "", "source_url": SOURCE_URL,
                "source_model_note": "No verified per-match value available; no imputation",
                "source_validation_status": "not_available_in_current_free_source",
                "source_reverification_note": "No current free source row matched this fixture.",
                "source_raw_row": "", "retrieved_at_utc": "",
            }
        output.append(row)
    fields = ["internal_match_id","provider_event_id","match_date","home_team","away_team","home_goals","away_goals",
              "home_xg","away_xg","home_xga","away_xga","xg_status","source_name","source_url",
              "source_model_note","source_validation_status","source_reverification_note","source_raw_row","retrieved_at_utc"]
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(output)
    audit = {
        "generated_at_utc": now, "matches_in_mapping": len(matches),
        "source_url": SOURCE_URL, "source_policy": "free public page only; no paid API",
        "source_counts_in_output": {name: sum(1 for r in output if r.get("source_name") == name) for name in sorted(set(r.get("source_name", "") for r in output))},
        "source_methodology": "Provider-specific xG; not official J.League xG. See source_name, source_url and source_model_note on each row.",
        "sources_in_output": sorted(set(r.get("source_name", "") for r in output if r.get("source_name"))),
        "source_urls_in_output": sorted(set(r.get("source_url", "") for r in output if r.get("source_url"))),
        "matches_with_xg": sum(bool(r["home_xg"]) for r in output),
        "matches_with_verified_xg": sum(r.get("source_validation_status") == "verified_from_current_fetch" for r in output),
        "matches_with_unverified_legacy_xg": sum(r.get("source_validation_status") == "unverified_legacy_value" for r in output),
        "matches_missing_xg": sum(not bool(r["home_xg"]) for r in output),
        "source_rows_matched": len(source_rows),
        "source_access_status": source_access_status,
        "source_access_detail": source_access_detail,
        "preserved_prior_values_when_source_blocked": source_access_status != "fetched",
        "missing_policy": "blank values only; no score-based or model-based imputation",
        "matches": [{"internal_match_id":r["internal_match_id"],"date":r["match_date"],"home":r["home_team"],"away":r["away_team"],"status":r["xg_status"]} for r in output],
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if k != "matches"}, ensure_ascii=False, indent=2))
    if audit["matches_with_xg"] == 0:
        print("WARNING: no per-match xG was available from the free public source; per-match fields remain blank. Team aggregates, if collected, are stored separately.")

if __name__ == "__main__":
    MATCHES = []
    main()
