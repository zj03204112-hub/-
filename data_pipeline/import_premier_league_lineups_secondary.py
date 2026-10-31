#!/usr/bin/env python3
"""Import a pinned, public EPL 2025/26 lineup dataset with explicit provenance.

The upstream dataset is used as a secondary lineup source only. It does not
overwrite Understat xG or the immutable baseline results. Ambiguous match joins
are quarantined rather than guessed.
"""
import csv, io, json, re, time
from pathlib import Path
from datetime import datetime
import requests

REPO = "olbauday/FPL-Core-Insights"
REF = "0e8b5033c329459d661b2511fa376b9d82954698"
API = f"https://api.github.com/repos/{REPO}/git/trees/{REF}?recursive=1"
RAW = f"https://raw.githubusercontent.com/{REPO}/{REF}/"
OUT = Path("data/season_match_detail")
OUT.mkdir(parents=True, exist_ok=True)
BASELINE = OUT / "premier-league_2025_enriched.csv"
LINEUPS_OUT = OUT / "premier-league_2025_lineups_secondary.csv"
AUDIT_OUT = OUT / "premier-league_2025_lineups_audit.json"
HEADERS = {"User-Agent": "football-model-data-audit/1.0"}

def norm(s):
    # Normalize suffixes/prefixes before aliases; otherwise e.g. "Brighton & Hove Albion FC"
    # misses the alias for "Brighton & Hove Albion".
    s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    for _ in range(2):
        for suffix in (" football club", " fc", " afc"):
            if s.endswith(suffix):
                s = s[:-len(suffix)].strip()
        for prefix in ("afc ",):
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
    aliases = {
        "afc bournemouth":"bournemouth", "bournemouth":"bournemouth",
        "brighton hove albion":"brighton", "brighton and hove albion":"brighton",
        "tottenham hotspur":"tottenham", "tottenham":"tottenham",
        "newcastle united":"newcastle", "newcastle":"newcastle",
        "west ham united":"west ham", "west ham":"west ham",
        "wolverhampton wanderers":"wolves", "wolverhampton":"wolves", "wolves":"wolves",
        "manchester united":"manchester united", "man utd":"manchester united",
        "manchester city":"manchester city", "man city":"manchester city",
        "leeds united":"leeds", "leeds":"leeds",
        "nottingham forest":"nottingham forest", "aston villa":"aston villa",
        "crystal palace":"crystal palace", "sunderland":"sunderland",
    }
    return aliases.get(s, s)

def get_text(url):
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.text

def read_csv_url(url):
    return list(csv.DictReader(io.StringIO(get_text(url))))

if not BASELINE.exists():
    raise SystemExit(f"Missing baseline enrichment file: {BASELINE}")
with BASELINE.open(encoding="utf-8-sig", newline="") as f:
    baseline = list(csv.DictReader(f))
if not baseline:
    raise SystemExit("Baseline CSV is empty")
base_by_pair = {}
for row in baseline:
    key = (norm(row.get("home_team")), norm(row.get("away_team")))
    base_by_pair.setdefault(key, []).append(row)

tree = requests.get(API, headers=HEADERS, timeout=60)
tree.raise_for_status()
entries = tree.json().get("tree", [])
fixture_paths = sorted(x["path"] for x in entries if x["path"].startswith("data/2025-2026/By Tournament/Premier League/GW") and x["path"].endswith("/fixtures.csv"))
lineup_paths = sorted(x["path"] for x in entries if x["path"].startswith("data/2025-2026/By Tournament/Premier League/GW") and x["path"].endswith("/lineups.csv"))
if len(fixture_paths) < 30 or len(lineup_paths) < 30:
    raise SystemExit(f"Unexpected upstream coverage: fixtures={len(fixture_paths)}, lineups={len(lineup_paths)}")

fixture_map, player_rows, match_audit, errors = {}, [], [], []\nupstream_pair_examples = []
for path in fixture_paths:
    try:
        for m in read_csv_url(RAW + requests.utils.requote_uri(path)):
            mid = m.get("match_id", "")
            key = (norm(m.get("home_team")), norm(m.get("away_team")))
            fixture_map[mid] = {
                "home": m.get("home_team",""), "away": m.get("away_team",""),
                "kickoff_time": m.get("kickoff_time",""), "gameweek": m.get("gameweek",""),
                "home_score": m.get("home_score",""), "away_score": m.get("away_score",""),
                "finished": m.get("finished",""), "pair": key
            }
    except Exception as e:
        errors.append({"path":path,"error":str(e)})

for path in lineup_paths:
    try:
        for p in read_csv_url(RAW + requests.utils.requote_uri(path)):
            mid = p.get("match_id", "")
            fx = fixture_map.get(mid)
            if not fx:
                errors.append({"path":path,"error":"lineup_match_id_missing_fixture","match_id":mid})
                continue
            pair = fx["pair"]
            candidates = base_by_pair.get(pair, [])
            # There should be exactly one EPL fixture per home/away pair in a season.
            join_status = "matched_unique_team_pair" if len(candidates) == 1 else ("unmatched_team_pair" if not candidates else "ambiguous_team_pair")
            if join_status != "matched_unique_team_pair":
                match_audit.append({"upstream_match_id":mid,"home":fx["home"],"away":fx["away"],"status":join_status,"baseline_candidates":len(candidates)})
                continue
            b = candidates[0]
            player_rows.append({
                "baseline_date":b.get("date",""), "home_team":b.get("home_team",""), "away_team":b.get("away_team",""),
                "understat_match_id":b.get("understat_match_id",""), "upstream_match_id":mid,
                "upstream_gameweek":fx["gameweek"], "upstream_kickoff_time":fx["kickoff_time"],
                "team_side":p.get("team_side",""), "player_id":p.get("player_id",""),
                "player_name":p.get("player_name",""), "position":p.get("position",""),
                "jersey_number":p.get("jersey_number",""), "is_starting":p.get("is_starting",""),
                "formation":p.get("formation",""), "lineup_status":p.get("lineup_status",""),
                "lineup_source_repo":REPO, "lineup_source_commit":REF
            })
    except Exception as e:
        errors.append({"path":path,"error":str(e)})

# Per-match lineup completeness: exactly 11 starters for each side, plus explicit status.
grouped = {}
for p in player_rows:
    k = (p["baseline_date"],p["home_team"],p["away_team"])
    grouped.setdefault(k, []).append(p)
complete_matches, invalid_matches = [], []
for k, ps in grouped.items():
    counts = {}
    for side in ("home","away"):
        side_rows = [p for p in ps if p["team_side"] == side]
        starters = sum(str(p["is_starting"]).lower() == "true" for p in side_rows)
        counts[side] = {"rows":len(side_rows),"starters":starters,"confirmed_rows":sum(p["lineup_status"]=="confirmed" for p in side_rows)}
    record = {"date":k[0],"home":k[1],"away":k[2],"home_counts":counts["home"],"away_counts":counts["away"]}
    if all(counts[s]["starters"] == 11 and counts[s]["confirmed_rows"] >= 11 for s in ("home","away")):
        complete_matches.append(record)
    else:
        invalid_matches.append(record)

with LINEUPS_OUT.open("w",encoding="utf-8",newline="") as f:
    fields = list(player_rows[0].keys()) if player_rows else ["baseline_date","home_team","away_team","upstream_match_id","team_side","player_name"]
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(player_rows)
audit = {
    "season":"2025-26","source":"FPL-Core-Insights public data integration review branch",
    "source_repo":f"https://github.com/{REPO}","source_commit":REF,
    "source_note":"Third-party secondary lineup dataset; pinned to a specific commit for reproducibility. This import does not overwrite the baseline results or Understat xG.",
    "baseline_match_rows":len(baseline),"upstream_fixture_files":len(fixture_paths),"upstream_lineup_files":len(lineup_paths),
    "upstream_fixture_ids":len(fixture_map),"lineup_player_rows_joined":len(player_rows),
    "unique_baseline_matches_with_lineup_rows":len(grouped),
    "complete_confirmed_starting_xi_matches":len(complete_matches),
    "invalid_or_incomplete_starting_xi_matches":len(invalid_matches),
    "unmatched_or_ambiguous_matches":len(match_audit),"fetch_or_join_errors":len(errors),
    "match_coverage_pct":round(100*len(grouped)/len(baseline),2) if baseline else 0,
    "complete_starting_xi_coverage_pct":round(100*len(complete_matches)/len(baseline),2) if baseline else 0,
    "complete_matches":complete_matches,"incomplete_matches":invalid_matches,
    "baseline_pair_examples":baseline_pair_examples,"upstream_pair_examples":upstream_pair_examples,\n    "unmatched_matches":match_audit,"errors":errors
}
AUDIT_OUT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({k:audit[k] for k in ["baseline_match_rows","upstream_fixture_files","upstream_lineup_files","lineup_player_rows_joined","unique_baseline_matches_with_lineup_rows","complete_confirmed_starting_xi_matches","invalid_or_incomplete_starting_xi_matches","unmatched_or_ambiguous_matches","fetch_or_join_errors","match_coverage_pct","complete_starting_xi_coverage_pct"]},ensure_ascii=False))
if len(complete_matches) == 0:
    raise SystemExit("No complete starting XIs verified; inspect audit and do not claim lineup coverage.")
