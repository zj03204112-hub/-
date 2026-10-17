#!/usr/bin/env python3
"""Small end-to-end smoke test for independently sourced football results.
A query miss is pending review, never proof that a match does not exist.
This test does not freeze the 300-match sample, tune models, or modify V5.1.
"""
from __future__ import annotations
import json, re
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

OUT = Path("data/sample_2026_300/structured_source_smoke_test.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; football-results-audit/1.1)"}
CASES = [
    {"league":"J1 League","date":"2026-08-08","home":["FC TOKYO","FC Tokyo"],"away":["FC Machida Zelvia","Machida"],"hg":1,"ag":5,
     "source":"J.League official match page","url":"https://www.jleague.jp/en/match/j1/2026/080802/"},
    {"league":"J1 League","date":"2026-08-08","home":["Kashiwa Reysol","柏レイソル"],"away":["Mito Hollyhock","水戸ホーリーホック"],"hg":2,"ag":1,
     "source":"J.League official schedule page","url":"https://www.jleague.jp/en/j1/match/search-list/?enddate=2026-08-09&period=custom&startdate=2026-08-07"},
    {"league":"K League 1","date":"2026-10-09","home":["Daejeon Hana","DAEJEON HANA","대전"],"away":["Jeonbuk","JEONBUK","전북"],"hg":2,"ag":1,
     "source":"K League official site","url":"https://www.kleague.com/index.do"},
    {"league":"K League 1","date":"2026-10-09","home":["Gangwon","GANGWON","강원"],"away":["Bucheon","BUCHEON","부천"],"hg":2,"ag":0,
     "source":"K League official schedule portal","url":"https://portal.kleague.com/main/schedule/calendar.do"},
]
def norm(s):
    return re.sub(r"[^0-9a-z가-힣ぁ-んァ-ヶ一-龯]+","",str(s).casefold())
def has_alias(text, aliases):
    n=norm(text)
    return any(norm(a) and norm(a) in n for a in aliases)
def fetch(case):
    r=requests.get(case["url"],headers=HEADERS,timeout=30)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"lxml")
    # Search bounded text blocks rather than assuming a particular table layout.
    blocks=[]
    for node in soup.select("tr, li, article, .match, .match-list, .schedule, .game"):
        txt=" ".join(node.stripped_strings)
        if txt and len(txt)<1500:
            blocks.append(txt)
    if not blocks:
        blocks=[soup.get_text(" ",strip=True)]
    matches=[]
    for txt in blocks:
        if case["date"] not in txt and not any(d in txt for d in [case["date"].replace("-","."),case["date"].replace("-","/")]):
            # K League calendar widgets sometimes omit year/date in each match block;
            # only allow the full page fallback below, never silently infer a date.
            continue
        if not has_alias(txt,case["home"]) or not has_alias(txt,case["away"]):
            continue
        scores=[]
        for m in re.finditer(r"(?<!\d)(\d{1,2})\s*[-–:：]\s*(\d{1,2})(?!\d)",txt):
            scores.append([int(m.group(1)),int(m.group(2))])
        if scores:
            matches.append({"text":txt,"score":scores[-1]})
    unique=[]
    seen=set()
    for m in matches:
        key=(norm(m["text"]),tuple(m["score"]))
        if key not in seen:
            unique.append(m); seen.add(key)
    if len(unique)!=1:
        return {"provider":case["source"],"request_status":"success","match_found":False,
                "score_match":False,"matches_found":len(unique),
                "status":"pending_review","note":"Request succeeded, but no unique date/team/score row was extracted; this is not evidence that the match did not exist."}
    m=unique[0]
    return {"provider":case["source"],"request_status":"success","match_found":True,
            "matches_found":1,"observed_score":m["score"],
            "score_match":m["score"]==[case["hg"],case["ag"]],
            "status":"pass" if m["score"]==[case["hg"],case["ag"]] else "score_mismatch",
            "evidence_text":m["text"][:500]}
def main():
    results=[]
    for case in CASES:
        try:
            result=fetch(case)
        except Exception as exc:
            result={"provider":case["source"],"request_status":"failed","match_found":None,
                    "score_match":None,"status":"source_failure","error":f"{type(exc).__name__}: {exc}"}
        results.append({"case":{k:v for k,v in case.items() if k not in {"url","home","away"}},
                        "url":case["url"],"result":result})
    passes=sum(x["result"].get("status")=="pass" for x in results)
    summary={"created_utc":datetime.now(timezone.utc).isoformat(),
             "purpose":"small J1/K League structured source E2E test",
             "case_count":len(CASES),
             "request_successes":sum(x["result"]["request_status"]=="success" for x in results),
             "request_failures":sum(x["result"]["request_status"]=="failed" for x in results),
             "identity_and_score_passes":passes,
             "all_cases_pass":passes==len(CASES),
             "results":results,
             "guardrails":"No 300-match freeze; no tuning/backtest; V5.1 unchanged."}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
