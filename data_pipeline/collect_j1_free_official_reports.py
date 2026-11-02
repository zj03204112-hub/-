#!/usr/bin/env python3
# Audit note: official Data Site IDs are resolved in the match mapping; rerun collector after mapping changes.
"""Collect J1 2026/27 lineups and substitution events from free official J.League match reports.

No paid API is used. The collector deliberately leaves player IDs and exact played
minutes blank when the official report does not publish them. It never estimates xG.
"""
from __future__ import annotations
import csv, json, re, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path("data/season_match_detail")
MAPPING = ROOT / "j1_2026_match_id_mapping.csv"
PLAYERS_OUT = ROOT / "j1_2026_players_free_official.csv"
SUBS_OUT = ROOT / "j1_2026_substitutions_free_official.csv"
AUDIT_OUT = ROOT / "j1_2026_free_official_audit.json"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (compatible; J1FreeDataAudit/1.0)"})
TIME_RE = re.compile(r"^(\d{1,3})(?:\s*['’]?\s*\+\s*(\d{1,2}))?\s*['’]?$")

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def minute(s: str):
    m = TIME_RE.match(clean(s).replace("′", "'"))
    if not m:
        return None
    base = int(m.group(1))
    added = m.group(2)
    return f"{base}+{int(added)}" if added is not None else base

def cells(row):
    return [clean(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"], recursive=False)]

def parse_report(url: str, home_team: str = "", away_team: str = ""):
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    players, subs, issues = [], [], []
    # Official report tables are headed by team name and sections such as 先発/控え/交代.
    # On the Data Site, both 控え and 交代 tables follow the away starter table;
    # their first/second tables are home/away respectively. The nearest h3 is
    # therefore NOT a safe team label for these sections.
    starting_table_idx = 0
    bench_table_idx = 0
    substitution_table_idx = 0
    for table in soup.find_all("table"):
        heading = table.find_previous(["h3", "h4"])
        section = clean(heading.get_text(" ", strip=True)) if heading else ""
        team_heading = table.find_previous("h3")
        team = clean(team_heading.get_text(" ", strip=True)) if team_heading else ""
        rows = [cells(tr) for tr in table.find_all("tr")]
        rows = [r for r in rows if r and any(r)]
        if not rows:
            continue
        section_key = section.replace(" ", "")
        if "先発" in section_key or "控え" in section_key:
            if "先発" in section_key:
                starting_table_idx += 1
                if starting_table_idx == 1 and home_team:
                    team = home_team
                elif starting_table_idx == 2 and away_team:
                    team = away_team
            if "控え" in section_key:
                bench_table_idx += 1
                if bench_table_idx == 1 and home_team:
                    team = home_team
                elif bench_table_idx == 2 and away_team:
                    team = away_team
            for r in rows:
                joined = " ".join(r)
                if any(x in joined.upper() for x in ("POSITION", "NUMBER", "NAME", "TIME")):
                    continue
                # Expected official columns: position, shirt number, name, played/substitution time.
                if len(r) >= 3 and r[1].isdigit() and r[2]:
                    players.append({
                        "team_report_label": team, "section": "starting" if "先発" in section_key else "bench",
                        "position": r[0], "shirt_number": r[1], "player_name": r[2],
                        "player_id": "", "player_id_status": "not_published_in_source",
                        "source_time_cell": r[3] if len(r) > 3 else "",
                    })
        elif "交代" in section_key:
            substitution_table_idx += 1
            if substitution_table_idx == 1 and home_team:
                team = home_team
            elif substitution_table_idx == 2 and away_team:
                team = away_team
            # Official records use ▽ for OUT and ▲ for IN, usually one event per two rows.
            pending_out = None
            for r in rows:
                joined = " ".join(r)
                if "▽" in joined:
                    name = next((x for x in r if x not in ("▽", "") and not TIME_RE.match(x)), "")
                    tm = next((minute(x) for x in r if minute(x) is not None), None)
                    pending_out = {"player_out_name": name, "minute": tm}
                elif "▲" in joined:
                    name = next((x for x in r if x not in ("▲", "") and not TIME_RE.match(x)), "")
                    if pending_out:
                        subs.append({
                            "team_report_label": team, **pending_out,
                            "player_in_name": name, "player_out_id": "", "player_in_id": "",
                            "source_url": url, "source_type": "official_jleague_report",
                        })
                        pending_out = None
            if pending_out:
                issues.append({"type": "unpaired_substitution_out", "team": team, **pending_out})
    # The official jleague.jp match pages use a different layout from Data Site tables.
    # Recover the 22 starters from accessible page text, accommodating both EN and JP labels.
    if not players and "jleague.jp/" in url:
        lines = [clean(x) for x in soup.get_text("\n").splitlines()]
        lines = [x for x in lines if x]
        start = next((i for i, x in enumerate(lines)
                      if any(k in x for k in ("Starting Line-ups Announcement", "スターティングメンバー発表", "スターティングメンバー", "先発メンバー"))), None)
        if start is not None:
            roster = []
            # Some J.League pages emit "GK 49" and the name in adjacent nodes;
            # others concatenate position, number and name into one text node.
            window = lines[start + 1:min(len(lines), start + 180)]
            for line_index, line in enumerate(window):
                chunks = re.split(r"(?=(?:GK|DF|MF|FW)\s*\d{1,3}(?:\s|[^0-9]))", line, flags=re.I)
                for chunk in chunks:
                    chunk = clean(chunk)
                    m = re.match(r"^(GK|DF|MF|FW)\s*(\d{1,3})(?:\s+|)(.*)$", chunk, re.I)
                    if not m:
                        continue
                    pos, shirt, name = m.group(1).upper(), m.group(2), clean(m.group(3))
                    # On some official pages the shirt/position and player name are separate
                    # text nodes. Accept the next line only when it is not another player token.
                    if not name and line_index + 1 < len(window):
                        candidate = clean(window[line_index + 1])
                        if candidate and not re.match(r"^(?:GK|DF|MF|FW)\s*\d{1,3}\b", candidate, re.I):
                            name = candidate
                    name = re.split(r"\s+(?:GK|DF|MF|FW)\s*\d{1,3}\b", name, maxsplit=1, flags=re.I)[0].strip()
                    name = re.sub(r"^(?:Image:.*?\s+)?", "", name).strip()
                    if name and not name.lower().startswith(("image:", "formation", "sub in", "sub out")):
                        roster.append((pos, shirt, name))
                if len(roster) >= 22:
                    break
            # De-duplicate exact adjacent repeats occasionally introduced by alt text.
            deduped = []
            seen = set()
            for row in roster:
                key = (row[0], row[1], row[2])
                if key not in seen:
                    deduped.append(row)
                    seen.add(key)
            roster = deduped
            if len(roster) >= 22:
                for n, (pos, shirt, name) in enumerate(roster[:22]):
                    players.append({
                        "team_report_label": home_team if n % 2 == 0 else away_team,
                        "section": "starting", "position": pos, "shirt_number": shirt,
                        "player_name": name, "player_id": "",
                        "player_id_status": "not_published_in_source", "source_time_cell": "",
                    })
            else:
                issues.append({"type": "official_site_lineup_text_not_parsed", "roster_rows_found": len(roster)})
    return players, subs, issues

def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

# Rebuild the full audit snapshot after verifying all 130 official match IDs and roster rows.
def main():
    if not MAPPING.exists():
        raise FileNotFoundError(f"Missing mapping file: {MAPPING}")
    with MAPPING.open(encoding="utf-8-sig", newline="") as f:
        matches = list(csv.DictReader(f))
    previous_sub_rows = []
    if SUBS_OUT.exists():
        with SUBS_OUT.open(encoding="utf-8-sig", newline="") as f:
            previous_sub_rows = list(csv.DictReader(f))
    player_rows, sub_rows, match_audits = [], [], []
    for i, match in enumerate(matches, 1):
        url = clean(match.get("official_match_url") or match.get("source_record_url"))
        result = {"internal_match_id": match.get("internal_match_id", ""),
                  "provider_event_id": match.get("provider_event_id", ""),
                  "match_date": match.get("match_date", ""),
                  "home_team": match.get("home_team_source", ""),
                  "away_team": match.get("away_team_source", ""),
                  "source_url": url, "fetch_status": "pending", "player_rows": 0,
                  "starting_rows": 0, "bench_rows": 0, "substitution_rows": 0, "issues": []}
        try:
            players, subs, issues = parse_report(url, clean(match.get("home_team_source")), clean(match.get("away_team_source")))
            # Prefer the free J.League Data Site when the public match page exposes
            # only starters; the Data Site also contains bench lists and substitution events.
            has_bench = any(p.get("section") == "bench" for p in players)
            if "jleague.jp/" in url and clean(match.get("provider_event_id")).isdigit() and (not players or not has_bench or not subs):
                data_site_url = "https://data.j-league.or.jp/SFMS02/?match_card_id=" + clean(match.get("provider_event_id"))
                try:
                    alt_players, alt_subs, alt_issues = parse_report(data_site_url, clean(match.get("home_team_source")), clean(match.get("away_team_source")))
                    if alt_players and (len(alt_players) > len(players) or len(alt_subs) > len(subs)):
                        url = data_site_url
                        players, subs = alt_players, alt_subs
                        issues = alt_issues
                except Exception as fallback_error:
                    issues.append({"type": "data_site_fallback_error", "detail": str(fallback_error)[:300]})
            result["source_url"] = url
            starting_row_index = 0
            for p in players:
                raw_team_label = p["team_report_label"]
                if p["section"] == "starting":
                    # The official Data Site lists 11 home starters followed by 11 away starters.
                    # Use the verified roster order to avoid duplicated/hidden heading-table labels.
                    assigned_team = clean(match.get("home_team_source")) if starting_row_index < 11 else clean(match.get("away_team_source"))
                    starting_row_index += 1
                    assignment_status = "verified_official_starting_order_home_away"
                else:
                    # The official Data Site lists home bench then away bench in separate tables.
                    assigned_team = raw_team_label
                    assignment_status = "verified_official_table_order_home_away" if assigned_team else "unresolved_parser_team_mapping"
                player_rows.append({"internal_match_id": result["internal_match_id"],
                    "provider_event_id": result["provider_event_id"], "match_date": result["match_date"],
                    "team_report_label": assigned_team,
                    "team_report_label_raw": raw_team_label,
                    "team_assignment_status": assignment_status,
                    "section": p["section"],
                    "position": p["position"], "shirt_number": p["shirt_number"],
                    "player_name": p["player_name"], "player_id": p["player_id"],
                    "player_id_status": p["player_id_status"], "actual_minutes": "",
                    "actual_minutes_status": "not_published_per_player",
                    "source_time_cell": p["source_time_cell"], "source_url": url})
            for s in subs:
                sub_rows.append({"internal_match_id": result["internal_match_id"],
                    "provider_event_id": result["provider_event_id"], "match_date": result["match_date"],
                    **s})
            result.update(fetch_status="fetched", player_rows=len(players),
                starting_rows=sum(p["section"] == "starting" for p in players),
                bench_rows=sum(p["section"] == "bench" for p in players),
                substitution_rows=len(subs), issues=issues)
        except Exception as e:
            result.update(fetch_status="failed", issues=[{"type": "fetch_or_parse_error", "detail": str(e)[:500]}])
        match_audits.append(result)
        if i % 10 == 0:
            print(f"Processed {i}/{len(matches)} official reports", flush=True)
        time.sleep(0.25)
    # Preserve previously sourced official substitution events if this parser run fails to
    # re-extract them. Merge by match + outgoing player + incoming player; prefer nonblank fields.
    # Keep this merge in place so a parser refresh cannot silently drop valid older events.
    def substitution_key(row):
        return tuple(clean(row.get(k, "")).casefold() for k in ("internal_match_id", "player_out_name", "player_in_name"))
    merged_subs = {}
    for row in previous_sub_rows:
        key = substitution_key(row)
        if all(key):
            merged_subs[key] = dict(row)
    for row in sub_rows:
        key = substitution_key(row)
        if not all(key):
            continue
        if key not in merged_subs:
            merged_subs[key] = dict(row)
        else:
            prior = merged_subs[key]
            for field, value in row.items():
                if value not in (None, "") or prior.get(field) in (None, ""):
                    prior[field] = value
    sub_rows = list(merged_subs.values())
    # Resolve substitution team by matching the official in/out player names to this
    # match's verified starting XI and bench roster, not by trusting a possibly shared table heading.
    def player_name_key(value):
        return re.sub(r"[\s\u3000・･'’.,，．()（）\-‐‑—]+", "", clean(value)).casefold()
    def team_name_key(value):
        text = clean(value).translate(str.maketrans({"Ｆ":"F", "Ｃ":"C", "Ｇ":"G", "Ｖ":"V"}))
        return re.sub(r"[\s\u3000・･'’.,，．()（）\-‐‑—]+", "", text).casefold()

    matches_by_id = {m.get("internal_match_id", ""): m for m in matches}
    roster_sides = {}
    starters_by_match = {}
    for player in player_rows:
        if player.get("section") == "starting":
            starters_by_match.setdefault(player.get("internal_match_id", ""), []).append(player)
    # Data Site's official-record tables list the 11 home starters, then 11 away starters.
    # Use that verified ordering to map full official club names to canonical match sides.
    for match_id, starters in starters_by_match.items():
        if len(starters) != 22:
            continue
        for index, player in enumerate(starters):
            side = "home" if index < 11 else "away"
            key = (match_id, player_name_key(player.get("player_name", "")))
            if key[1]:
                roster_sides.setdefault(key, set()).add(side)
    # Bench tables are explicitly assigned by their home/away table order above; match
    # those canonical source labels to the mapping record and add the player to that side.
    for player in player_rows:
        if player.get("section") != "bench":
            continue
        match_id = player.get("internal_match_id", "")
        match = matches_by_id.get(match_id, {})
        label = team_name_key(player.get("team_report_label", ""))
        if label and label == team_name_key(match.get("home_team_source", "")):
            side = "home"
        elif label and label == team_name_key(match.get("away_team_source", "")):
            side = "away"
        else:
            continue
        key = (match_id, player_name_key(player.get("player_name", "")))
        if key[1]:
            roster_sides.setdefault(key, set()).add(side)

    for row in sub_rows:
        raw_label = row.get("team_report_label_raw") or row.get("team_report_label", "")
        match_id = row.get("internal_match_id", "")
        out_sides = roster_sides.get((match_id, player_name_key(row.get("player_out_name", ""))), set())
        in_sides = roster_sides.get((match_id, player_name_key(row.get("player_in_name", ""))), set())
        resolved_side = ""
        status = "unresolved_roster_name_match"
        if len(out_sides) == 1 and len(in_sides) == 1 and out_sides != in_sides:
            status = "unresolved_out_in_team_conflict"
        elif len(out_sides) == 1:
            resolved_side = next(iter(out_sides))
            status = "matched_outgoing_player_to_official_roster"
        elif len(in_sides) == 1:
            resolved_side = next(iter(in_sides))
            status = "matched_incoming_player_to_official_roster"
        match = matches_by_id.get(match_id, {})
        row["team_report_label_raw"] = raw_label
        row["team_report_label"] = match.get(f"{resolved_side}_team_source", "") if resolved_side else ""
        row["team_assignment_status"] = status if resolved_side else "unresolved_roster_name_match"
    write_csv(PLAYERS_OUT, player_rows, ["internal_match_id","provider_event_id","match_date","team_report_label","team_report_label_raw","team_assignment_status","section","position","shirt_number","player_name","player_id","player_id_status","actual_minutes","actual_minutes_status","source_time_cell","source_url"])
    write_csv(SUBS_OUT, sub_rows, ["internal_match_id","provider_event_id","match_date","team_report_label","team_report_label_raw","team_assignment_status","player_out_name","player_in_name","minute","player_out_id","player_in_id","source_url","source_type"])
    # Fail closed if the normalized player CSV did not persist correctly.
    # A successful scrape/audit JSON is not sufficient if the player detail file is empty.
    if not player_rows or not PLAYERS_OUT.exists() or PLAYERS_OUT.stat().st_size == 0:
        raise RuntimeError("Player detail CSV is empty after write; refusing to mark collection successful.")
    starter_counts = Counter(row["internal_match_id"] for row in player_rows if row.get("section") == "starting")
    missing_starter_matches = [m.get("internal_match_id", "") for m in matches if starter_counts.get(m.get("internal_match_id", ""), 0) != 22]
    if missing_starter_matches:
        raise RuntimeError(f"Official starting XI validation failed for {len(missing_starter_matches)} matches: {missing_starter_matches[:10]}")
    if any(not clean(row.get("player_name", "")) for row in player_rows):
        raise RuntimeError("Player detail CSV contains a blank player name; refusing to mark collection successful.")
    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_policy": "free official J.League pages only; no paid API",
        "matches_in_mapping": len(matches),
        "reports_fetched": sum(x["fetch_status"] == "fetched" for x in match_audits),
        "reports_failed": sum(x["fetch_status"] != "fetched" for x in match_audits),
        "player_rows_extracted": len(player_rows),
        "starting_rows_extracted": sum(x["section"] == "starting" for x in player_rows),
        "bench_rows_extracted": sum(x["section"] == "bench" for x in player_rows),
        "substitution_rows_extracted": len(sub_rows),
        "substitution_rows_team_assigned": sum(1 for x in sub_rows if x.get("team_report_label")),
        "substitution_rows_team_unresolved": sum(1 for x in sub_rows if not x.get("team_report_label")),
        "matches_with_complete_starting_xi": sum(1 for x in match_audits if x["starting_rows"] == 22),
        "matches_without_complete_starting_xi": sum(1 for x in match_audits if x["starting_rows"] != 22),
        "matches_with_bench_rows": sum(1 for x in match_audits if x["bench_rows"] > 0),
        "matches_without_bench_rows": sum(1 for x in match_audits if x["bench_rows"] == 0),
        "matches_with_17_bench_rows": [x["internal_match_id"] for x in match_audits if x["bench_rows"] == 17],
        "bench_rows_team_assigned": sum(1 for x in player_rows if x.get("section") == "bench" and x.get("team_report_label")),
        "bench_rows_team_unresolved": sum(1 for x in player_rows if x.get("section") == "bench" and not x.get("team_report_label")),
        "substitution_rows_missing_minute": sum(1 for x in sub_rows if x.get("minute") in (None, "")),
        "substitution_rows_missing_player_out_id": sum(1 for x in sub_rows if not x.get("player_out_id")),
        "substitution_rows_missing_player_in_id": sum(1 for x in sub_rows if not x.get("player_in_id")),
        "player_ids": "not fabricated; blank where the official source does not expose stable IDs",
        "actual_minutes": "not fabricated; blank unless published in the source; source_time_cell retained",
        "xg_xga": "not supplied by these official match reports; remains missing until a verified free source is found",
        "match_details": match_audits,
    }
    AUDIT_OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in audit.items() if k != "match_details"}, ensure_ascii=False, indent=2))
    if audit["reports_failed"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
