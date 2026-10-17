#!/usr/bin/env python3
"""Probe public historical result providers on a tiny J1/K League sample only.

This is a source-access/E2E diagnostic, not a 300-match verifier. A source outage
is recorded as unavailable; it never marks a candidate as unmatched.
"""
from __future__ import annotations
import json, re, time
from datetime import datetime, timezone
from pathlib import Path
import requests

OUT = Path("data/sample_2026_300/public_source_probe.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; football-source-probe/1.0)"}
TIMEOUT = 20

# Known fixtures are used only as source probes; both must be rechecked against
# the provider response before that provider can be considered usable.
CASES = [
    {"league":"J1 League","date":"2026-08-08","home":"FC Tokyo","away":"Machida Zelvia","home_goals":1,"away_goals":5,
     "espn_sport":"soccer","espn_league":"jpn.1"},
    {"league":"K League 1","date":"2026-10-09","home":"Daejeon Hana Citizen","away":"Jeonbuk Hyundai Motors","home_goals":2,"away_goals":1,
     "espn_sport":"soccer","espn_league":"kor.1"},
]

def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s).casefold())

def probe_espn(case):
    url = f"https://site.api.espn.com/apis/site/v2/sports/{case['espn_sport']}/{case['espn_league']}/scoreboard?dates={case['date'].replace('-', '')}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        events = payload.get("events", [])
        observed = []
        for event in events:
            comp = (event.get("competitions") or [{}])[0]
            competitors = comp.get("competitors") or []
            teams = {c.get("homeAway"): c for c in competitors}
            h, a = teams.get("home", {}), teams.get("away", {})
            observed.append({
                "name":event.get("name"),
                "date":event.get("date"),
                "status":((comp.get("status") or {}).get("type") or {}).get("name"),
                "home":(h.get("team") or {}).get("displayName"),
                "away":(a.get("team") or {}).get("displayName"),
                "home_goals":h.get("score"), "away_goals":a.get("score"),
            })
        matched = [e for e in observed if norm(e["home"]) == norm(case["home"]) and norm(e["away"]) == norm(case["away"])]
        if len(matched) == 1:
            e = matched[0]
            score_ok = str(e["home_goals"]) == str(case["home_goals"]) and str(e["away_goals"]) == str(case["away_goals"])
            return {"provider":"ESPN public scoreboard API","url":url,"request_status":"success","events_returned":len(events),
                    "match_found":True,"identity_match":True,"score_match":score_ok,"event":e,
                    "note":"Score match is diagnostic only; confirm status/date and 90-minute score semantics before production use."}
        return {"provider":"ESPN public scoreboard API","url":url,"request_status":"success","events_returned":len(events),
                "match_found":False,"identity_match":False,"score_match":False,"note":"Query succeeded but no exact home/away match was returned; not an API failure."}
    except Exception as exc:
        return {"provider":"ESPN public scoreboard API","url":url,"request_status":"failed","error":f"{type(exc).__name__}: {exc}",
                "match_found":None,"identity_match":None,"score_match":None}

def probe_html(provider, url, case, home_aliases, away_aliases):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        body = re.sub(r"\\s+", " ", r.text)
        text = re.sub(r"<[^>]+>", " ", body)
        text = re.sub(r"\\s+", " ", text)
        hn = [norm(x) for x in home_aliases]
        an = [norm(x) for x in away_aliases]
        compact = norm(text)
        identity = any(x and x in compact for x in hn) and any(x and x in compact for x in an)
        score = f"{case['home_goals']}-{case['away_goals']}"
        score_seen = score in text or f"{case['home_goals']}:{case['away_goals']}" in text
        return {"provider":provider,"url":url,"request_status":"success","http_status":r.status_code,
                "match_found":identity,"identity_match":identity,"score_match":bool(identity and score_seen),
                "note":"HTML presence test only; requires structured row-level extraction before production verification."}
    except Exception as exc:
        return {"provider":provider,"url":url,"request_status":"failed","error":f"{type(exc).__name__}: {exc}",
                "match_found":None,"identity_match":None,"score_match":None}

def main():
    results=[]
    for case in CASES:
        results.append({"case":case,"probe":probe_espn(case)})
        if case["league"] == "J1 League":
            results.append({"case":case,"probe":probe_html(
                "J.League Data Site (official)",
                "https://data.j-league.or.jp/SFMS01/search?competition_years=2026&home_away_select=0",
                case, ["FC Tokyo","FC東京","東京ＦＣ"], ["Machida Zelvia","Machida","町田","町田ゼルビア"])})
        else:
            results.append({"case":case,"probe":probe_html(
                "K League official schedule",
                "https://www.kleague.com/schedule.do?leagueId=1&month=10&year=2026",
                case, ["Daejeon Hana Citizen","Daejeon","대전"], ["Jeonbuk Hyundai Motors","Jeonbuk","전북"])})
    summary={
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "purpose":"small historical-source probe only; not full candidate verification",
        "case_count":len(CASES),"probe_count":len(results),
        "provider_request_successes":sum(x["probe"]["request_status"]=="success" for x in results),
        "provider_request_failures":sum(x["probe"]["request_status"]=="failed" for x in results),
        "exact_score_matches":sum(x["probe"].get("score_match") is True for x in results),
        "results":results,
        "guardrail":"No sample freeze, no model tuning/backtest, no V5.1 changes."
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    # A probe report is useful even if some providers block CI; do not fail the job
    # solely because a provider is temporarily unavailable.
if __name__=="__main__": main()
