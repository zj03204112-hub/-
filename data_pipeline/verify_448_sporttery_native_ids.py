#!/usr/bin/env python3
"""Verify Sporttery native match IDs for the frozen 448-row backtest sample.
Only assign an ID when date + normalized home/away + final score produce exactly
one candidate. Never guess or silently choose the first candidate.
"""
import csv, json, re, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "model_validation/v52_jincai_20260901_20261013/v52_plus_nations_league_448_merged_backtest_20261013.csv"
OUT = ROOT / "model_validation/v52_jincai_20260901_20261013/v52_448_native_id_mapping_20261013.csv"
REPORT = ROOT / "model_validation/v52_jincai_20260901_20261013/v52_448_native_id_audit_20261013.json"
BASE = "https://webapi.sporttery.cn/gateway/uniform/fb/getMatchDataPageListV1.qry"
HEAD = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Referer": "https://m.sporttery.cn/mjc/zqsj/?tab=result",
    "Origin": "https://m.sporttery.cn",
    "Accept": "application/json,text/plain,*/*",
    "X-Requested-With": "XMLHttpRequest",
}

def norm(s):
    return "".join(c.lower() for c in (s or "") if c.isalnum() or "\u4e00" <= c <= "\u9fff")

def get_score(m):
    for hk, ak in (("sectionsNo999", None), ("homeScore", "awayScore"), ("allHomeScore", "allAwayScore")):
        if ak:
            try:
                return int(m[hk]), int(m[ak])
            except (KeyError, TypeError, ValueError):
                pass
        else:
            vals = re.findall(r"\d+", str(m.get(hk) or ""))
            if len(vals) >= 2:
                try:
                    return int(vals[-2]), int(vals[-1])
                except ValueError:
                    pass
    return None, None

def get_date(m):
    raw = str(m.get("matchDate") or m.get("match_date") or "")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw[:10]

def fetch_date(day):
    params = urllib.parse.urlencode({"method": "result", "matchDate": day})
    req = urllib.request.Request(BASE + "?" + params, headers=HEAD)
    with urllib.request.urlopen(req, timeout=35) as response:
        payload = json.load(response)
    val = payload.get("value") or {}
    return [m for group in (val.get("matchInfoList") or []) for m in (group.get("subMatchList") or [])]

with SRC.open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
assert len(rows) == 448, f"Expected 448 source rows, got {len(rows)}"

by_date = defaultdict(list)
errors = {}
for r in rows:
    by_date[r["date"]].append(r)
for day in sorted(by_date):
    try:
        by_date[day] = fetch_date(day)
        time.sleep(0.15)
    except Exception as e:
        errors[day] = f"{type(e).__name__}: {e}"
        by_date[day] = []

out = []
status_counts = Counter()
used_ids = defaultdict(list)
for r in rows:
    day = r["date"]
    target_h, target_a = norm(r["home_team"]), norm(r["away_team"])
    try:
        sh, sa = int(r["actual_home_goals"]), int(r["actual_away_goals"])
    except (ValueError, TypeError):
        sh = sa = None
    candidates = []
    for m in by_date[day]:
        mid = str(m.get("matchId") or "").strip()
        mh = norm(m.get("allHomeTeam") or m.get("homeTeamAllName") or m.get("homeTeam") or "")
        ma = norm(m.get("allAwayTeam") or m.get("awayTeamAllName") or m.get("awayTeam") or "")
        ms = get_score(m)
        if mid and mh == target_h and ma == target_a and ms == (sh, sa):
            candidates.append((m, ms))
    # Exact candidates only. Ambiguous matches are deliberately left unresolved.
    base = {
        "source_match_id": r.get("source_match_id", ""),
        "sample_source": r.get("sample_source", ""),
        "date": day, "home_team": r["home_team"], "away_team": r["away_team"],
        "actual_score": r["actual_score"], "sporttery_match_id": "",
        "matched_home": "", "matched_away": "", "matched_score": "",
        "candidate_count": len(candidates), "status": ""
    }
    if day in errors:
        base["status"] = "QUERY_ERROR"
        base["evidence"] = errors[day]
    elif len(candidates) == 1:
        m, ms = candidates[0]
        base["sporttery_match_id"] = str(m.get("matchId"))
        base["matched_home"] = m.get("allHomeTeam") or m.get("homeTeamAllName") or m.get("homeTeam") or ""
        base["matched_away"] = m.get("allAwayTeam") or m.get("awayTeamAllName") or m.get("awayTeam") or ""
        base["matched_score"] = f"{ms[0]}-{ms[1]}"
        base["status"] = "VERIFIED_UNIQUE"
        used_ids[base["sporttery_match_id"]].append(base)
    elif len(candidates) > 1:
        base["status"] = "AMBIGUOUS"
    else:
        base["status"] = "NO_EXACT_MATCH"
    status_counts[base["status"]] += 1
    out.append(base)

# Detect duplicate use of one native ID across different sample rows; invalidate all such assignments.
for mid, entries in used_ids.items():
    if len(entries) > 1:
        for e in entries:
            e["status"] = "DUPLICATE_ID_COLLISION"
            e["sporttery_match_id"] = ""
        status_counts["VERIFIED_UNIQUE"] -= len(entries)
        status_counts["DUPLICATE_ID_COLLISION"] += len(entries)

fields = ["source_match_id","sample_source","date","home_team","away_team","actual_score","sporttery_match_id","matched_home","matched_away","matched_score","candidate_count","status","evidence"]
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(out)
report = {
    "title": "448场样本 Sporttery 原生 matchId 核验",
    "sample_rows": len(rows),
    "queried_dates": len(by_date),
    "date_query_errors": errors,
    "status_counts": dict(status_counts),
    "verified_unique_count": sum(1 for r in out if r["status"] == "VERIFIED_UNIQUE"),
    "unresolved_count": sum(1 for r in out if r["status"] != "VERIFIED_UNIQUE"),
    "duplicate_native_ids": {mid: len(v) for mid, v in used_ids.items() if len(v) > 1},
    "matching_rule": "exact normalized date + home team + away team + final score; only a unique candidate is accepted",
    "policy": "No guessed IDs. No exact or ambiguous candidates remain blank. Sporttery native ID is a provider-specific ID and is not substituted for internal DB match_id.",
    "source_csv": str(SRC.relative_to(ROOT)),
    "mapping_csv": str(OUT.relative_to(ROOT))
}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
