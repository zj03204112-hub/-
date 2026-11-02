#!/usr/bin/env python3
"""Save publicly displayed J1 team-season xG/xGA aggregates, separate from match-level data."""
import csv, json, re
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

URL = "https://www.oddalerts.com/xg/j-league"
OUT = Path("data/season_match_detail/j1_2026_team_season_xg_free.csv")
AUDIT = Path("data/season_match_detail/j1_2026_team_season_xg_free_audit.json")

def clean(s):
    return re.sub(r"\\s+", " ", (s or "")).strip()

def parse_tables(soup, now):
    teams = {}
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if len(trs) < 2:
            continue
        headers = [clean(x.get_text(" ", strip=True)).lower() for x in trs[0].find_all(["th","td"])]
        if not headers or not any(h == "team" for h in headers):
            continue
        idx = {h:i for i,h in enumerate(headers)}
        xg_key = next((h for h in headers if h == "xg"), None)
        xga_key = next((h for h in headers if h == "xga"), None)
        per_key = next((h for h in headers if h in ("/90","xg /90","xg/90")), None)
        xga_per_key = next((h for h in headers if h in ("xga /90","xga/90")), None)
        for tr in trs[1:]:
            cells = [clean(x.get_text(" ", strip=True)) for x in tr.find_all(["th","td"])]
            if len(cells) < len(headers):
                continue
            team = cells[idx["team"]] if idx["team"] < len(cells) else ""
            if not team or team == "#":
                continue
            row = teams.setdefault(team, {
                "team_name_source": team, "played": "", "goals_for": "",
                "season_xg": "", "xg_per90": "", "goals_minus_xg": "",
                "season_xga": "", "xga_per90": "", "source_name": "OddAlerts",
                "source_url": URL, "source_model_note": "Public team-season aggregate; NOT match-level xG; provider-specific model",
                "retrieved_at_utc": now
            })
            def val(key):
                return cells[idx[key]] if key and key in idx and idx[key] < len(cells) else ""
            if xg_key:
                row["season_xg"] = val(xg_key)
                row["played"] = val("p")
                row["goals_for"] = val("g")
                row["xg_per90"] = val(per_key)
                row["goals_minus_xg"] = val("+/-")
            if xga_key:
                row["season_xga"] = val(xga_key)
                row["xga_per90"] = val(xga_per_key) or val(per_key)
    return list(teams.values())

def main():
    now = datetime.now(timezone.utc).isoformat()
    err = ""
    rows = []
    try:
        r = requests.get(URL, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        rows = parse_tables(soup, now)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:500]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["team_name_source","played","goals_for","season_xg","xg_per90","goals_minus_xg","season_xga","xga_per90","source_name","source_url","source_model_note","retrieved_at_utc"]
    if rows:
        with OUT.open("w", encoding="utf-8-sig", newline="") as f:
            w=csv.DictWriter(f, fieldnames=fields);w.writeheader();w.writerows(rows)
    audit = {
        "generated_at_utc": now, "source_url": URL, "source_access": "success" if rows else "failed_or_no_parse",
        "source_error": err, "teams_with_public_season_aggregates": len(rows),
        "policy": "free public page; no paid API; no extrapolation from season averages to match-level values",
        "interpretation": "This dataset is team-season aggregate xG/xGA. It must not be merged into per-match xG fields or represented as per-match values."
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if not rows:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
