#!/usr/bin/env python3
"""Independent phase-one audit for the J1 2026 season collection output.

Reads the API-Football pipeline's j1_2026.csv and j1_2026_players.csv,
writes a machine-readable per-fixture audit, and fails on identity/duplicate
integrity errors. Missing provider data is reported, never fabricated.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("data/season_match_detail")
MATCH_FILE = ROOT / "j1_2026.csv"
PLAYER_FILE = ROOT / "j1_2026_players.csv"
REPORT_FILE = ROOT / "j1_2026_phase1_audit.json"
FINISHED = {"FT", "AET", "PEN"}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required collection output missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def as_int(value: object) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def norm_name(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def main() -> int:
    matches = read_csv(MATCH_FILE)
    players = read_csv(PLAYER_FILE)
    by_fixture: dict[str, list[dict[str, str]]] = defaultdict(list)
    for p in players:
        by_fixture[str(p.get("fixture_id", "")).strip()].append(p)

    issues: list[dict[str, object]] = []
    completed = [m for m in matches if str(m.get("status", "")).upper() in FINISHED]
    score_count = sum(
        as_int(m.get("home_goals_90m")) is not None and as_int(m.get("away_goals_90m")) is not None
        for m in completed
    )
    fixture_ids = [str(m.get("fixture_id", "")).strip() for m in matches]
    duplicate_fixture_ids = [k for k, n in Counter(fixture_ids).items() if not k or n > 1]

    xi_both = 0
    appearance_both = 0
    xg_both = 0
    xi_counts: dict[str, dict[str, int]] = {}
    player_key_rows: list[tuple[str, str, str]] = []
    name_key_rows: list[tuple[str, str, str]] = []
    for m in matches:
        fid = str(m.get("fixture_id", "")).strip()
        status = str(m.get("status", "")).upper()
        group = by_fixture.get(fid, [])
        home_id = str(m.get("home_team", "")).strip()
        away_id = str(m.get("away_team", "")).strip()
        if status in FINISHED:
            if as_int(m.get("home_goals_90m")) is None or as_int(m.get("away_goals_90m")) is None:
                issues.append({"fixture_id": fid, "type": "missing_score", "detail": "Completed fixture has no numeric full-time score."})
            counts = {
                side: sum(1 for p in group if str(p.get("team_side", "")).lower() == side and truthy(p.get("is_starting")))
                for side in ("home", "away")
            }
            xi_counts[fid] = counts
            if counts["home"] == 11 and counts["away"] == 11:
                xi_both += 1
            else:
                issues.append({"fixture_id": fid, "type": "starting_xi_count", "detail": counts})
            if str(m.get("home_players_appeared", "")).strip() not in {"", "None", "null"} and str(m.get("away_players_appeared", "")).strip() not in {"", "None", "null"}:
                appearance_both += 1
            else:
                issues.append({"fixture_id": fid, "type": "appearance_coverage", "detail": "Missing appeared-player list for one or both teams."})
            if str(m.get("home_xg", "")).strip() not in {"", "None", "null"} and str(m.get("away_xg", "")).strip() not in {"", "None", "null"}:
                xg_both += 1
            else:
                issues.append({"fixture_id": fid, "type": "xg_coverage", "detail": "Missing xG for one or both teams."})

        for p in group:
            team_id = str(p.get("team_id", "")).strip()
            player_id = str(p.get("player_id", "")).strip()
            player_name = str(p.get("player_name", "")).strip()
            side = str(p.get("team_side", "")).strip().lower()
            if side not in {"home", "away"}:
                issues.append({"fixture_id": fid, "type": "unknown_team_side", "detail": {"team_id": team_id, "team_name": p.get("team_name"), "side": side, "player": player_name}})
            if not player_name:
                issues.append({"fixture_id": fid, "type": "missing_player_name", "detail": {"team_id": team_id, "player_id": player_id}})
            # The provider player ID is preferred; name is the fallback only when ID is absent.
            player_key_rows.append((fid, team_id, player_id or norm_name(player_name)))
            name_key_rows.append((fid, team_id, norm_name(player_name)))
            minutes = as_int(p.get("minutes_played"))
            appeared_value = str(p.get("appeared", "")).strip().lower()
            if minutes is not None and ((minutes > 0 and appeared_value == "false") or (minutes <= 0 and appeared_value == "true")):
                issues.append({"fixture_id": fid, "type": "appearance_flag_conflict", "detail": {"player": player_name, "minutes_played": minutes, "appeared": appeared_value}})
            if appeared_value == "true" and minutes is None:
                issues.append({"fixture_id": fid, "type": "appeared_without_minutes", "detail": {"player": player_name}})

    key_counts = Counter(player_key_rows)
    name_counts = Counter(name_key_rows)
    duplicate_player_keys = [list(k) for k, n in key_counts.items() if n > 1]
    duplicate_normalized_names = [list(k) for k, n in name_counts.items() if k[2] and n > 1]
    for key in duplicate_player_keys:
        issues.append({"fixture_id": key[0], "type": "duplicate_fixture_team_player_key", "detail": key})
    for key in duplicate_normalized_names:
        issues.append({"fixture_id": key[0], "type": "duplicate_fixture_team_normalized_name", "detail": key})

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "league": "J1 League",
        "season_start_year": 2026,
        "source": "API-Football collection output; official J.League mapping cross-check is a separate source layer",
        "input_files": {"matches": str(MATCH_FILE), "players": str(PLAYER_FILE)},
        "coverage": {
            "fixtures_total": len(matches),
            "fixture_ids_unique_nonempty": len(matches) - len(duplicate_fixture_ids),
            "completed_fixtures": len(completed),
            "completed_with_numeric_score": score_count,
            "completed_with_both_teams_exactly_11_starting_xi": xi_both,
            "completed_with_both_teams_appearance_lists": appearance_both,
            "completed_with_both_team_xg": xg_both,
            "normalized_player_rows": len(players),
            "unique_fixture_team_player_keys": len(set(player_key_rows)),
            "duplicate_fixture_team_player_keys": len(duplicate_player_keys),
            "duplicate_fixture_team_normalized_names": len(duplicate_normalized_names),
        },
        "duplicate_fixture_ids": duplicate_fixture_ids,
        "starting_xi_counts_by_fixture": xi_counts,
        "issues_count": len(issues),
        "issues": issues,
        "acceptance": {
            "identity_and_duplicate_integrity_pass": not duplicate_fixture_ids and not duplicate_player_keys and not duplicate_normalized_names,
            "all_completed_scores_present": score_count == len(completed),
            "all_completed_fixtures_have_both_exact_11_xi": xi_both == len(completed),
            "all_completed_fixtures_have_appearance_lists": appearance_both == len(completed),
            "all_completed_fixtures_have_both_xg": xg_both == len(completed),
            "coverage_gaps_are_reported_not_imputed": True,
        },
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Integrity violations fail the workflow. Coverage gaps remain explicit and
    # are reviewed from the JSON report rather than being filled with guesses.
    if duplicate_fixture_ids or duplicate_player_keys or duplicate_normalized_names:
        print("AUDIT FAILED: duplicate/empty fixture identity or duplicate player keys/names.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
