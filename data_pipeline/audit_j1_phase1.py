#!/usr/bin/env python3
"""Quality gate for the J1 season-detail collection output. No imputation."""
from __future__ import annotations
import csv, json
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path("data/season_match_detail")
STEM = "j1_2026"

def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def truthy(value):
    return str(value).strip().lower() in {"true", "1", "yes"}

def norm(value):
    return " ".join(str(value or "").casefold().split())

def parse_json_list(value):
    try:
        obj = json.loads(value) if value else None
        return obj if isinstance(obj, list) else None
    except (TypeError, json.JSONDecodeError):
        return None

def main():
    match_path = ROOT / f"{STEM}.csv"
    player_path = ROOT / f"{STEM}_players.csv"
    missing = [str(p) for p in (match_path, player_path) if not p.exists()]
    if missing:
        raise SystemExit("Required collection output missing: " + ", ".join(missing))

    matches = read_csv(match_path)
    players = read_csv(player_path)
    finished = [r for r in matches if r.get("status") in {"FT", "AET", "PEN"}]
    scored = [r for r in finished if r.get("home_goals_90m", "") != "" and r.get("away_goals_90m", "") != ""]

    both_xi = []
    xi_bad = []
    for row in finished:
        home_xi = parse_json_list(row.get("home_starting_xi"))
        away_xi = parse_json_list(row.get("away_starting_xi"))
        if home_xi is not None and away_xi is not None and len(home_xi) == 11 and len(away_xi) == 11:
            both_xi.append(row)
        else:
            xi_bad.append({"fixture_id": row.get("fixture_id"), "home_count": None if home_xi is None else len(home_xi),
                           "away_count": None if away_xi is None else len(away_xi),
                           "status": row.get("lineup_status")})

    keys = [(r.get("fixture_id"), r.get("team_id"), r.get("player_id") or norm(r.get("player_name"))) for r in players]
    names = [(r.get("fixture_id"), r.get("team_id"), norm(r.get("player_name"))) for r in players]
    key_counts, name_counts = Counter(keys), Counter(names)
    duplicate_keys = [{"fixture_id": k[0], "team_id": k[1], "player_key": k[2], "rows": n}
                      for k, n in key_counts.items() if n > 1]
    duplicate_names = [{"fixture_id": k[0], "team_id": k[1], "normalized_name": k[2], "rows": n}
                       for k, n in name_counts.items() if n > 1 and k[2]]

    by_fixture_side = {}
    for row in players:
        by_fixture_side.setdefault((row.get("fixture_id"), row.get("team_side")), []).append(row)
    starter_counts = {}
    for key, group in by_fixture_side.items():
        starter_counts[key] = sum(truthy(r.get("is_starting")) for r in group)
    exact_xi_fixtures = sorted(fid for fid in {r.get("fixture_id") for r in players}
        if starter_counts.get((fid, "home")) == 11 and starter_counts.get((fid, "away")) == 11)

    appearance_sides = set()
    for row in finished:
        fid = str(row.get("fixture_id"))
        home = parse_json_list(row.get("home_players_appeared"))
        away = parse_json_list(row.get("away_players_appeared"))
        if home is not None and away is not None:
            appearance_sides.add(fid)

    xg_complete = [r for r in finished if r.get("home_xg", "") != "" and r.get("away_xg", "") != ""]
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "league": "J1 League", "season_start_year": 2026,
        "source_match_csv": str(match_path), "source_player_csv": str(player_path),
        "fixture_rows": len(matches), "finished_fixtures": len(finished),
        "finished_with_score": len(scored),
        "finished_with_both_teams_exactly_11_starting_xi": len(both_xi),
        "finished_missing_or_incomplete_xi_count": len(xi_bad),
        "xi_incomplete_examples": xi_bad[:100],
        "normalized_player_rows": len(players),
        "unique_fixture_team_player_keys": len(set(keys)),
        "duplicate_fixture_team_player_key_count": len(duplicate_keys),
        "duplicate_fixture_team_player_key_examples": duplicate_keys[:100],
        "duplicate_fixture_team_normalized_name_count": len(duplicate_names),
        "duplicate_fixture_team_normalized_name_examples": duplicate_names[:100],
        "fixtures_with_both_teams_player_stats_response": len(appearance_sides),
        "finished_with_both_xg": len(xg_complete),
        "finished_missing_xg_count": len(finished) - len(xg_complete),
        "finished_missing_xg_fixture_ids": [r.get("fixture_id") for r in finished if r not in xg_complete][:200],
        "acceptance_checks": {
            "no_duplicate_fixture_team_player_keys": len(duplicate_keys) == 0,
            "no_duplicate_fixture_team_normalized_names": len(duplicate_names) == 0,
            "all_finished_fixtures_have_both_teams_exactly_11_xi": len(both_xi) == len(finished),
            "all_finished_fixtures_have_both_teams_player_stats_response": len(appearance_sides) == len(finished),
            "all_finished_fixtures_have_both_xg": len(xg_complete) == len(finished),
        },
        "limitations": [
            "This report validates provider output shape and coverage; it does not replace official per-match lineup/name verification.",
            "xGA is taken from the opponent's same-match xG field in the collection output.",
            "Missing provider values are reported and never imputed."
        ]
    }
    out = ROOT / f"{STEM}_phase1_audit.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Data coverage can be incomplete and should be reported; only structural duplicates are a hard failure.
    if duplicate_keys or duplicate_names:
        raise SystemExit("Audit failed: duplicate player keys/names detected.")
if __name__ == "__main__":
    main()
