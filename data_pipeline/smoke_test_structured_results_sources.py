#!/usr/bin/env python3
"""End-to-end smoke test of structured independent football results.

Only a tiny J1/K League 1 sample is queried. A request failure is distinct from
a successful query with no matching row. This does not freeze data or run models.
"""
from __future__ import annotations
import json, re
from io import StringIO
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests

OUT = Path("data/sample_2026_300/structured_source_smoke_test.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; football-results-audit/1.0)"}
CASES = [
    {"league":"J1 League","date":"2026-08-08","home":["FC東京","FC Tokyo"],"away":["町田","FC町田ゼルビア","Machida"],"hg":1,"ag":5,
     "source":"J.League Data Site","url":"https://data.j-league.or.jp/SFMS01/search?competition_years=2026&home_away_select=0"},
    {"league":"J1 League","date":"2026-08-15","home":["ヴィッセル神戸","神戸","Vissel Kobe"],"away":["FC東京","FC Tokyo"],"hg":2,"ag":2,
     "source":"J.League Data Site","url":"https://data.j-league.or.jp/SFMS01/search?competition_years=2026&home_away_select=0"},
    {"league":"K League 1","date":"2026-10-09","home":["대전 하나","대전","Daejeon Hana"],"away":["전북","Jeonbuk"],"hg":2,"ag":1,
     "source":"FBref K League schedule","url":"https://fbref.com/en/comps/55/schedule/K-League-1-Scores-and-Fixtures"},
    {"league":"K League 1","date":"2026-10-09","home":["강원","Gangwon"],"away":["부천","Bucheon"],"hg":2,"ag":0,
     "source":"365Scores K League match page","url":"https://www.365scores.com/football/match/k-league-1-618/bucheon-fc-1995-gangwon-fc-9825-11361-618"},
]

def norm(x):
    return re.sub(r"[^0-9a-z가-힣ぁ-んァ-ヶ一-龯]+", "", str(x).casefold())

def first_match(text, aliases):
    t = norm(text)
    return any(norm(a) and norm(a) in t for a in aliases)

def probe_jleague(case):
    r = requests.get(case["url"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    tables = pd.read_html(StringIO(r.text))
    target_date = pd.Timestamp(case["date"])
    matches = []
    for table in tables:
        # Official result table normally exposes date, home, score, away columns.
        cols = [str(c).strip() for c in table.columns]
        for _, row in table.iterrows():
            vals = {str(cols[i]): str(row.iloc[i]) for i in range(min(len(cols), len(row)))}
            joined = " | ".join(vals.values())
            if not first_match(joined, case["home"]) or not first_match(joined, case["away"]):
                continue
            score_text = next((v for k,v in vals.items() if "スコア" in k or "score" in k.casefold()), "")
            date_text = next((v for k,v in vals.items() if "試合日" in k or "date" in k.casefold()), "")
            nums = re.search(r"(\d+)\s*[-－]\s*(\d+)", score_text)
            date_ok = case["date"] in date_text or target_date.strftime("%y/%m/%d") in date_text or target_date.strftime("%Y/%m/%d") in date_text
            if nums:
                matches.append({"row":vals,"date_ok":date_ok,"score":[int(nums.group(1)),int(nums.group(2))]})
    if len(matches) != 1:
        return {"provider":case["source"],"request_status":"success","matches_found":len(matches),"match_found":False,"score_match":False,"note":"Query succeeded, but structured exact-row matching did not yield exactly one match."}
    m=matches[0]
    return {"provider":case["source"],"request_status":"success","matches_found":1,"match_found":True,
            "date_match":m["date_ok"],"home_away_match":True,"observed_score":m["score"],
            "score_match":m["score"]==[case["hg"],case["ag"]],"row":m["row"]}

def probe_fbref(case):
    r=requests.get(case["url"],headers=HEADERS,timeout=30)
    r.raise_for_status()
    tables=pd.read_html(StringIO(r.text))
    matches=[]
    for table in tables:
        df=table.copy()
        df.columns=[str(c).strip() for c in df.columns]
        cols={c.casefold():c for c in df.columns}
        date_col=next((v for k,v in cols.items() if k in {"date","day"}),None)
        home_col=next((v for k,v in cols.items() if k in {"home","home team"}),None)
        away_col=next((v for k,v in cols.items() if k in {"away","away team","opponent"}),None)
        score_col=next((v for k,v in cols.items() if k=="score"),None)
        if not all([date_col,home_col,away_col,score_col]): continue
        for _,row in df.iterrows():
            date=pd.to_datetime(str(row[date_col]),errors="coerce")
            if pd.isna(date) or date.strftime("%Y-%m-%d") != case["date"]: continue
            home=str(row[home_col]); away=str(row[away_col]); score=str(row[score_col])
            if first_match(home,case["home"]) and first_match(away,case["away"]):
                nums=re.search(r"(\d+)\s*[–-]\s*(\d+)",score)
                matches.append({"date":date.strftime("%Y-%m-%d"),"home":home,"away":away,
                                "score":[int(nums.group(1)),int(nums.group(2))] if nums else None})
    if len(matches)!=1:
        return {"provider":case["source"],"request_status":"success","matches_found":len(matches),"match_found":False,"score_match":False,"note":"Query succeeded but no unique structured row was found."}
    m=matches[0]
    return {"provider":case["source"],"request_status":"success","matches_found":1,"match_found":True,
            "date_match":m["date"]==case["date"],"home_away_match":True,"observed_score":m["score"],
            "score_match":m["score"]==[case["hg"],case["ag"]],"row":m}

def probe_365(case):
    r=requests.get(case["url"],headers=HEADERS,timeout=30)
    r.raise_for_status()
    text=re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",r.text))
    # Only claim success if the page contains the two team aliases and score.
    identity=first_match(text,case["home"]) and first_match(text,case["away"])
    score_pattern=re.search(rf"\b{case['hg']}\s*[-–:]\s*{case['ag']}\b",text)
    return {"provider":case["source"],"request_status":"success","match_found":identity,
            "score_match":bool(identity and score_pattern),"note":"HTML evidence test; if not exact, leave pending and do not mark unmatched."}

def main():
    results=[]
    for case in CASES:
        try:
            if case["league"]=="J1 League":
                result=probe_jleague(case)
            elif "FBref" in case["source"]:
                result=probe_fbref(case)
            else:
                result=probe_365(case)
        except Exception as exc:
            result={"provider":case["source"],"request_status":"failed","error":f"{type(exc).__name__}: {exc}",
                    "match_found":None,"score_match":None}
        results.append({"case":{k:v for k,v in case.items() if k not in {"url","home","away"}},
                        "url":case["url"],"result":result})
    summary={"created_utc":datetime.now(timezone.utc).isoformat(),"purpose":"small J1/K League structured source E2E test",
             "case_count":len(CASES),"request_successes":sum(x["result"]["request_status"]=="success" for x in results),
             "request_failures":sum(x["result"]["request_status"]=="failed" for x in results),
             "identity_and_score_passes":sum(x["result"].get("match_found") is True and x["result"].get("score_match") is True and x["result"].get("date_match",True) is True for x in results),
             "results":results,"guardrails":"No 300-match freeze; no tuning/backtest; V5.1 unchanged."}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
