#!/usr/bin/env python3
"""Aggregate only the verified per-match xG rows already present in the J1 dataset.

These are sums/means over the covered match sample, not full-season totals and not
independent official xGA. Missing per-match values are excluded and counted.
"""
import csv, json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path("data/season_match_detail")
MATCH=ROOT/"j1_2026_match_xg_free.csv"
OUT=ROOT/"j1_2026_team_xg_from_covered_matches.csv"
AUDIT=ROOT/"j1_2026_team_xg_from_covered_matches_audit.json"

def main():
    now=datetime.now(timezone.utc).isoformat()
    with MATCH.open(encoding="utf-8-sig",newline="") as f:
        rows=list(csv.DictReader(f))
    teams=defaultdict(lambda: {"match_count":0,"missing_xg_rows":0,"xg_sum":0.0,"xga_sum":0.0,"sources":set(),"source_urls":set()})
    for r in rows:
        for team,xg,xga in ((r.get("home_team",""),r.get("home_xg",""),r.get("home_xga","")),
                            (r.get("away_team",""),r.get("away_xg",""),r.get("away_xga",""))):
            if not team:
                continue
            d=teams[team]
            if not xg or not xga:
                d["missing_xg_rows"]+=1
                continue
            d["match_count"]+=1
            d["xg_sum"]+=float(xg)
            d["xga_sum"]+=float(xga)
            if r.get("source_name"): d["sources"].add(r["source_name"])
            if r.get("source_url"): d["source_urls"].add(r["source_url"])
    output=[]
    for team,d in sorted(teams.items()):
        n=d["match_count"]
        output.append({
            "team_name":team,"covered_matches_with_xg":n,
            "xg_sum_covered_matches":f'{d["xg_sum"]:.3f}' if n else "",
            "xga_sum_covered_matches":f'{d["xga_sum"]:.3f}' if n else "",
            "xg_avg_per_covered_match":f'{d["xg_sum"]/n:.3f}' if n else "",
            "xga_avg_per_covered_match":f'{d["xga_sum"]/n:.3f}' if n else "",
            "missing_team_match_rows":d["missing_xg_rows"],
            "source_names":";".join(sorted(d["sources"])),
            "source_urls":";".join(sorted(d["source_urls"])),
            "data_scope":"only matches present in j1_2026_match_xg_free.csv; NOT full season",
            "retrieved_at_utc":now
        })
    fields=["team_name","covered_matches_with_xg","xg_sum_covered_matches","xga_sum_covered_matches",
            "xg_avg_per_covered_match","xga_avg_per_covered_match","missing_team_match_rows",
            "source_names","source_urls","data_scope","retrieved_at_utc"]
    with OUT.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(output)
    audit={
        "generated_at_utc":now,"match_rows_read":len(rows),
        "match_rows_with_both_sides_xg":sum(bool(r.get("home_xg") and r.get("away_xg")) for r in rows),
        "teams_aggregated":len(output),
        "total_team_match_observations":sum(d["match_count"] for d in teams.values()),
        "policy":"derived only by summing source xG/xGA values; no imputation",
        "warning":"These are aggregates over the mapped match sample only, not a complete season. Per-match xGA is the opponent's same-match xG; source model is provider-specific, not official J.League.",
        "source_names":sorted({s for d in teams.values() for s in d["sources"]})
    }
    AUDIT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False,indent=2))
    if not rows:
        raise SystemExit("No match-level xG CSV found; refusing to create empty aggregate.")

if __name__=="__main__": main()
