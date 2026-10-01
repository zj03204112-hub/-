#!/usr/bin/env python3
import csv, json, time, re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import requests

REPO = Path(__file__).resolve().parents[1]
MAP = REPO / "model_validation/500_match_to_db_match_id_mapping.csv"
OUT_MAP = REPO / "model_validation/500_match_sporttery_matchid_mapping.csv"
OUT_REPORT = REPO / "model_validation/500_match_sporttery_odds_coverage.json"

BASE_RESULT = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
BASE_FIXED = "https://webapi.sporttery.cn/gateway/uniform/football/getFixedBonusV1.qry"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145 Safari/537.36",
    "Referer": "https://www.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}

def norm(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s

def date_only(s):
    return (s or "")[:10]

def fetch_json(session, url, params, retries=3):
    last = None
    for i in range(retries):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = str(e)
            time.sleep(1.5 * (i + 1))
    return None

def load_targets():
    with MAP.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 500:
        raise RuntimeError(f"冻结映射行数异常: {len(rows)}")
    ids = [r["match_id"] for r in rows]
    if len(set(ids)) != 500:
        raise RuntimeError("冻结DB match_id不是500个唯一值")
    return rows

def build_index(match_result):
    idx = defaultdict(list)
    for m in match_result:
        md = date_only(m.get("matchDate") or m.get("matchDateTime") or "")
        home = m.get("homeTeam") or m.get("allHomeTeam") or ""
        away = m.get("awayTeam") or m.get("allAwayTeam") or ""
        ss = m.get("sectionsNo999") or ""
        # Keep raw object; score parsing is intentionally permissive.
        hs = as_score(m, "home")
        aw = as_score(m, "away")
        key = (md, norm(home), norm(away), hs, aw)
        idx[key].append(m)
    return idx

def as_score(m, side):
    candidates = []
    sec = m.get("sectionsNo999")
    if isinstance(sec, dict):
        candidates += [sec.get("homeScore"), sec.get("awayScore")]
    if isinstance(sec, list):
        for x in sec:
            if isinstance(x, dict):
                candidates += [x.get("homeScore"), x.get("awayScore"), x.get("home"), x.get("away")]
    if isinstance(sec, str):
        nums = re.findall(r"\d+", sec)
        if len(nums) >= 2:
            return int(nums[-2 if side == "home" else -1])
    for k in (side + "Score", side + "Goals"):
        if m.get(k) not in (None, ""):
            try: return int(m[k])
            except: pass
    return -1

def match_target(t, candidates):
    # Exact date/team/score first, then date/team, then team/score.
    td = date_only(t["db_kickoff"])
    hs, aw = int(t["db_home_score"]), int(t["db_away_score"])
    home, away = norm(t["db_home"]), norm(t["db_away"])
    exact = []
    date_team = []
    team_score = []
    for m in candidates:
        md = date_only(m.get("matchDate") or m.get("matchDateTime") or "")
        mh = norm(m.get("homeTeam") or m.get("allHomeTeam") or "")
        ma = norm(m.get("awayTeam") or m.get("allAwayTeam") or "")
        ms = as_score(m, "home")
        mas = as_score(m, "away")
        if md == td and mh == home and ma == away and ms == hs and mas == aw:
            exact.append(m)
        if md == td and mh == home and ma == away:
            date_team.append(m)
        if mh == home and ma == away and ms == hs and mas == aw:
            team_score.append(m)
    for pool, method in ((exact,"date_team_score"),(date_team,"date_team"),(team_score,"team_score")):
        if len(pool) == 1:
            return pool[0], method
    return None, "unmatched" if not exact else "ambiguous"

def main():
    targets = load_targets()
    dates = sorted({date_only(r["db_kickoff"]) for r in targets})
    session = requests.Session()

    by_date = {}
    fetch_failures = []
    for d in dates:
        data = fetch_json(session, BASE_RESULT, {"startDate": d, "endDate": d})
        if not data or not data.get("success") or not data.get("value"):
            fetch_failures.append(d)
            by_date[d] = []
        else:
            by_date[d] = data["value"].get("matchResult", []) or []
        time.sleep(0.15)

    results = []
    sporttery_ids = {}
    for t in targets:
        d = date_only(t["db_kickoff"])
        m, method = match_target(t, by_date.get(d, []))
        row = dict(t)
        row.update({
            "sporttery_match_id": "",
            "sporttery_match_date": "",
            "sporttery_home": "",
            "sporttery_away": "",
            "sporttery_final_score": "",
            "match_method": method,
            "hhad_count": 0,
            "goal_lines": "",
            "initial_goal_line": "",
            "final_goal_line": "",
            "initial_h": "",
            "initial_d": "",
            "initial_a": "",
            "final_h": "",
            "final_d": "",
            "final_a": "",
            "odds_status": "NO_SPORTTERY_MATCH",
            "odds_error": ""
        })
        if m:
            sid = str(m.get("matchId") or m.get("matchID") or "")
            row["sporttery_match_id"] = sid
            row["sporttery_match_date"] = str(m.get("matchDate") or "")
            row["sporttery_home"] = str(m.get("homeTeam") or m.get("allHomeTeam") or "")
            row["sporttery_away"] = str(m.get("awayTeam") or m.get("allAwayTeam") or "")
            row["sporttery_final_score"] = f"{as_score(m,'home')}-{as_score(m,'away')}"
            sporttery_ids[t["match_id"]] = sid
        results.append(row)

    for i, row in enumerate(results, 1):
        sid = row["sporttery_match_id"]
        if not sid:
            continue
        data = fetch_json(session, BASE_FIXED, {"clientCode": "3001", "matchId": sid})
        if not data or not data.get("success") or not data.get("value"):
            row["odds_status"] = "FIXED_BONUS_FETCH_FAILED"
            row["odds_error"] = "no_success_value"
            continue
        oh = data["value"].get("oddsHistory") or {}
        hhad = oh.get("hhadList") or []
        row["hhad_count"] = len(hhad)
        if not hhad:
            row["odds_status"] = "NO_HHAD_LIST"
            continue
        valid = [x for x in hhad if x.get("goalLine") not in (None, "")]
        if not valid:
            row["odds_status"] = "HHAD_NO_GOALLINE"
            continue
        first, last = valid[0], valid[-1]
        row["goal_lines"] = "|".join(str(x.get("goalLine")) for x in valid)
        row["initial_goal_line"] = str(first.get("goalLine",""))
        row["final_goal_line"] = str(last.get("goalLine",""))
        row["initial_h"], row["initial_d"], row["initial_a"] = first.get("h",""), first.get("d",""), first.get("a","")
        row["final_h"], row["final_d"], row["final_a"] = last.get("h",""), last.get("d",""), last.get("a","")
        row["odds_status"] = "OK"
        time.sleep(0.12)

    with OUT_MAP.open("w", encoding="utf-8-sig", newline="") as f:
        fields = list(results[0].keys())
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(results)

    counts = defaultdict(int)
    for r in results:
        counts[r["odds_status"]] += 1
    report = {
        "target_count": len(results),
        "unique_db_match_id": len(set(r["match_id"] for r in results)),
        "unique_sporttery_match_id": len(set(r["sporttery_match_id"] for r in results if r["sporttery_match_id"])),
        "date_count": len(dates),
        "result_date_fetch_failures": fetch_failures,
        "status_counts": dict(counts),
        "sporttery_match_coverage": sum(bool(r["sporttery_match_id"]) for r in results),
        "hhad_coverage": sum(r["odds_status"] == "OK" for r in results),
        "hhad_goalLine_coverage": sum(r["odds_status"] == "OK" for r in results),
        "acceptance_gate": {
            "sporttery_match_500": sum(bool(r["sporttery_match_id"]) for r in results) == 500,
            "hhad_500": sum(r["odds_status"] == "OK" for r in results) == 500,
        }
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
