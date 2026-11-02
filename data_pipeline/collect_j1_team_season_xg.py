#!/usr/bin/env python3
"""Collect public ScanGoal J1 team-season xG/xGA totals; never infer missing values."""
import csv, json, re
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

URL = "https://scangoal.com/league/j-league"
OUT = Path("data/season_match_detail/j1_2026_team_season_xg_free.csv")
AUDIT = Path("data/season_match_detail/j1_2026_team_season_xg_free_audit.json")

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def main():
    now = datetime.now(timezone.utc).isoformat()
    rows_out, error = [], ""
    try:
        response = requests.get(URL, timeout=35, headers={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        selected = None
        for table in soup.find_all("table"):
            trs = table.find_all("tr")
            if len(trs) < 2:
                continue
            headers = [clean(x.get_text(" ", strip=True)).lower() for x in trs[0].find_all(["th","td"])]
            if "team" in headers and "xg" in headers and "xga" in headers and "p" in headers:
                selected = (trs, headers)
                break
        if selected:
            trs, headers = selected
            idx = {h:i for i,h in enumerate(headers)}
            for tr in trs[1:]:
                cells = [clean(x.get_text(" ", strip=True)) for x in tr.find_all(["th","td"])]
                if len(cells) < len(headers):
                    continue
                team = cells[idx["team"]]
                if not team or team == "#":
                    continue
                played = cells[idx["p"]]
                xg = cells[idx["xg"]]
                xga = cells[idx["xga"]]
                try:
                    n = float(played); xgf = float(xg); xgaf = float(xga)
                except ValueError:
                    continue
                rows_out.append({
                    "team_name_source":team,"matches_played_source":played,
                    "season_xg_total":f"{xgf:.3f}","season_xga_total":f"{xgaf:.3f}",
                    "xg_per_match":f"{xgf/n:.3f}" if n else "",
                    "xga_per_match":f"{xgaf/n:.3f}" if n else "",
                    "source_name":"ScanGoal","source_url":URL,
                    "source_model_note":"Provider-specific season aggregate, not official J.League xG; distinct from per-match CSV",
                    "retrieved_at_utc":now
                })
        if not rows_out:
            error = "Public page fetched but the expected Team/P/xG/xGA table was not parsed."
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    fields = ["team_name_source","matches_played_source","season_xg_total","season_xga_total","xg_per_match","xga_per_match","source_name","source_url","source_model_note","retrieved_at_utc"]
    if rows_out:
        with OUT.open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows_out)
    audit={
        "generated_at_utc":now,"source_url":URL,
        "source_access":"success" if rows_out else "failed_or_no_parse",
        "source_error":error,"teams_with_public_season_aggregates":len(rows_out),
        "policy":"free public page only; no paid API; no imputation",
        "interpretation":"Team-season aggregate totals and averages only; never fill missing match-level xG/xGA with these aggregates."
    }
    Path("data/season_match_detail").mkdir(parents=True,exist_ok=True)
    OUT_AUDIT=Path("data/season_match_detail/j1_2026_team_season_xg_free_audit.json")
    OUT_AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False,indent=2),flush=True)
    # A source block/parser mismatch should not prevent match-level xG data from being saved.
if __name__=="__main__":
    main()
