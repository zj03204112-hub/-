#!/usr/bin/env python3
"""Collect publicly visible J1 match xG from FootyStats without paid APIs.

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
SOURCE_URL = "https://footystats.org/japan/j1-league/xg"
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
    # Public page currently exposes a recent-results panel. Find the smallest DOM
    # elements containing a completed score, both teams and two decimal xG values.
    candidates = []
    tags = soup.find_all(["tr", "li", "article", "div", "a"])
    for el in tags:
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not txt or len(txt) > 1800:
            continue
        score = re.search(r"(?<!\d)(\d{1,2})\s*[-–]\s*(\d{1,2})(?!\d)", txt)
        decimals = re.findall(r"(?<![\d.])(\d+\.\d{1,3})(?![\d.])", txt)
        if not score or len(decimals) < 2:
            continue
        candidates.append((len(txt), txt, int(score.group(1)), int(score.group(2)), float(decimals[0]), float(decimals[1])))
    # For each fixture, retain the smallest text node/row that has enough data.
    out, seen = [], set()
    for _, txt, hg, ag, hxg, axg in sorted(candidates, key=lambda x: x[0]):
        compact = norm(txt)
        pair = None
        for match in MATCHES:
            home_alias = team_aliases(match["home_team_source"])
            away_alias = team_aliases(match["away_team_source"])
            if contains_team(txt, home_alias) and contains_team(txt, away_alias):
                if (match["home_goals"] or "").strip() and (match["away_goals"] or "").strip():
                    if int(float(match["home_goals"])) == hg and int(float(match["away_goals"])) == ag:
                        pair = match
                        break
        if pair is None:
            continue
        mid = pair["internal_match_id"]
        if mid in seen:
            continue
        # The source page lists fixture, home xG, away xG, then final goals.
        out.append({"match": pair, "home_xg": hxg, "away_xg": axg, "raw_text": txt})
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
    source_access_status = "not_attempted"
    source_access_detail = ""
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
                "xg_status": "published_on_free_public_source", "source_name": "FootyStats",
                "source_url": SOURCE_URL, "source_model_note": "Provider-specific xG formula; not official J.League xG",
                "source_raw_row": found["raw_text"], "retrieved_at_utc": now,
            }
        elif old.get("home_xg") and old.get("away_xg"):
            # Preserve previously sourced and audited values if the public page blocks automation.
            row = dict(old)
            row.update({"internal_match_id": mid, "provider_event_id": m.get("provider_event_id",""),
                "match_date": m.get("match_date",""), "home_team": m.get("home_team_source",""),
                "away_team": m.get("away_team_source",""), "home_goals": m.get("home_goals",""),
                "away_goals": m.get("away_goals","")})
        else:
            row = {
                "internal_match_id": mid, "provider_event_id": m.get("provider_event_id",""),
                "match_date": m.get("match_date",""), "home_team": m.get("home_team_source",""),
                "away_team": m.get("away_team_source",""), "home_goals": m.get("home_goals",""),
                "away_goals": m.get("away_goals",""), "home_xg": "", "away_xg": "",
                "home_xga": "", "away_xga": "", "xg_status": "missing_not_exposed_in_free_source",
                "source_name": "FootyStats", "source_url": SOURCE_URL,
                "source_model_note": "No verified per-match value available; no imputation",
                "source_raw_row": "", "retrieved_at_utc": "",
            }
        output.append(row)
    fields = ["internal_match_id","provider_event_id","match_date","home_team","away_team","home_goals","away_goals",
              "home_xg","away_xg","home_xga","away_xga","xg_status","source_name","source_url",
              "source_model_note","source_raw_row","retrieved_at_utc"]
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(output)
    audit = {
        "generated_at_utc": now, "matches_in_mapping": len(matches),
        "source_url": SOURCE_URL, "source_policy": "free public page only; no paid API",
        "source_methodology": "FootyStats states its J1 xG formula combines shot accuracy, shot frequency, attack dangerousness, possession and attack pressure; values are provider-specific, not official J.League xG.",
        "matches_with_xg": sum(bool(r["home_xg"]) for r in output),
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
        raise SystemExit("No xG rows mapped; inspect source HTML before accepting the collector.")

if __name__ == "__main__":
    MATCHES = []
    main()
