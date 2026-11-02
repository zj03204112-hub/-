#!/usr/bin/env python3
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
TIME_RE = re.compile(r"^(\d{1,3})(?:\s*\+\s*(\d{1,2}))?['’]?$")

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def minute(s: str):
    m = TIME_RE.match(clean(s).replace("′", "'"))
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2) or 0)

def cells(row):
    return [clean(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"], recursive=False)]

def parse_report(url: str, home_team: str = "", away_team: str = ""):
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    players, subs, issues = [], [], []
    # Official report tables are headed by team name and sections such as 先発/控え/交代.
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
        lines = [clean(x) for x in soup.get_text("\\n").splitlines()]
        lines = [x for x in lines if x]
        start = next((i for i, x in enumerate(lines)
                      if any(k in x for k in ("Starting Line-ups Announcement", "スターティングメンバー発表", "スターティングメンバー", "先発メンバー"))), None)
        if start is not None:
            roster = []
            # Some J.League pages emit "GK 49" and the name in adjacent nodes;
            # others concatenate position, number and name into one text node.
            for line in lines[start + 1:min(len(lines), start + 180)]:
                chunks = re.split(r"(?=(?:GK|DF|MF|FW)\\s*\\d{1,3}(?:\\s|[^0-9]))", line, flags=re.I)
                for chunk in chunks:
                    chunk = clean(chunk)
                    m = re.match(r"^(GK|DF|MF|FW)\\s*(\\d{1,3})(?:\\s+|)(.*)$", chunk, re.I)
                    if not m:
                        continue
                    pos, shirt, name = m.group(1).upper(), m.group(2), clean(m.group(3))
                    # If name is in a following text node, use that node; do not treat
                    # the next player's position as part of this player's name.
                    if not name and len(roster) < 22:
                        continue
                    name = re.split(r"\\s+(?:GK|DF|MF|FW)\\s*\\d{1,3}\\b", name, maxsplit=1, flags=re.I)[0].strip()
                    name = re.sub(r"^(?:Image:.*?\\s+)?", "", name).strip()
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

def main():
    if not MAPPING.exists():
        raise FileNotFoundError(f"Missing mapping file: {MAPPING}")
    with MAPPING.open(encoding="utf-8-sig", newline="") as f:
        matches = list(csv.DictReader(f))
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
            for p in players:
                player_rows.append({"internal_match_id": result["internal_match_id"],
                    "provider_event_id": result["provider_event_id"], "match_date": result["match_date"],
                    "team_report_label": p["team_report_label"], "section": p["section"],
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
    write_csv(PLAYERS_OUT, player_rows, ["internal_match_id","provider_event_id","match_date","team_report_label","section","position","shirt_number","player_name","player_id","player_id_status","actual_minutes","actual_minutes_status","source_time_cell","source_url"])
    write_csv(SUBS_OUT, sub_rows, ["internal_match_id","provider_event_id","match_date","team_report_label","player_out_name","player_in_name","minute","player_out_id","player_in_id","source_url","source_type"])
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
