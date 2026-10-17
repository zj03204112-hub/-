#!/usr/bin/env python3
"""Fetch official Sporttery Jincai handicap snapshots for recovered 2026-10-11 fixtures.
Read-only: writes only a new audit file; never rewrites prediction records or frozen sample."""
import json, re, time, urllib.parse, urllib.request
from pathlib import Path

SOURCE = Path("predictions/2026-10-11/recovered_original_predictions_004-028.md")
OUT = Path("reviews/2026-10-12/official_sporttery_handicap_audit_004-028.json")
BASE = "https://webapi.sporttery.cn/gateway/uniform/football"
RESULT = BASE + "/getUniformMatchResultV1.qry"
FIXED = BASE + "/getFixedBonusV1.qry"
HEADERS = {"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
           "Referer":"https://www.sporttery.cn/","Accept":"application/json, text/plain, */*"}

def fetch(url, params):
    req=urllib.request.Request(url+"?"+urllib.parse.urlencode(params), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)

def norm(s):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (s or "").lower())

def parse_table():
    text=SOURCE.read_text(encoding="utf-8")
    rows=[]
    for line in text.splitlines():
        m=re.match(r"\|\s*(\d{3})\s*\|\s*(.*?)—(.*?)\s*\|.*?\|\s*(\d+-\d+)\s*\|\s*(\d+-\d+)\s*\|.*?\|\s*([+-]?\d+)\s*\|", line)
        if m:
            rows.append({"match_no":m.group(1),"home":m.group(2).strip(),"away":m.group(3).strip(),
                         "first_score":m.group(4),"second_score":m.group(5),"recovered_handicap":int(m.group(6))})
    if len(rows) != 25:
        raise RuntimeError(f"Expected 25 recovered fixtures, parsed {len(rows)}")
    return rows

def score(m):
    raw=m.get("sectionsNo999") or m.get("fullScore") or m.get("score") or ""
    if isinstance(raw, dict):
        return raw.get("homeScore"), raw.get("awayScore")
    nums=re.findall(r"\d+", str(raw))
    return (int(nums[-2]), int(nums[-1])) if len(nums)>=2 else (None,None)

def main():
    rows=parse_table()
    # This endpoint's historical match list may expose results but not original pre-match lines.
    listing=fetch(RESULT, {"startDate":"2026-10-11","endDate":"2026-10-12"})
    val=listing.get("value") or {}
    matches=val.get("matchResult") or []
    output=[]
    for t in rows:
        candidates=[]
        for m in matches:
            home=m.get("homeTeam") or m.get("allHomeTeam") or ""
            away=m.get("awayTeam") or m.get("allAwayTeam") or ""
            if norm(home)==norm(t["home"]) and norm(away)==norm(t["away"]):
                candidates.append(m)
        rec=dict(t, candidates=len(candidates), official_match_id=None,
                 official_hhad_count=0, official_handicap_values=[], status="UNRESOLVED_MATCH")
        if len(candidates)==1:
            m=candidates[0]
            rec["official_match_id"]=str(m.get("matchId") or m.get("matchID") or "")
            rec["official_home"]=m.get("homeTeam") or m.get("allHomeTeam")
            rec["official_away"]=m.get("awayTeam") or m.get("allAwayTeam")
            rec["official_final_score"]=score(m)
            try:
                fixed=fetch(FIXED, {"clientCode":"3001","matchId":rec["official_match_id"]})
                fv=fixed.get("value") or {}
                hist=((fv.get("oddsHistory") or {}).get("hhadList") or [])
                valid=[x for x in hist if x.get("goalLine") not in (None,"")]
                rec["official_hhad_count"]=len(hist)
                rec["official_handicap_values"]=[{"goalLine":x.get("goalLine"),"h":x.get("h"),"d":x.get("d"),"a":x.get("a"),
                                                  "raw":x} for x in valid]
                rec["status"]="OFFICIAL_HHAD_FOUND" if valid else "NO_OFFICIAL_HHAD_HISTORY"
            except Exception as e:
                rec["status"]="OFFICIAL_FIXED_FETCH_FAILED"
                rec["error"]=str(e)
        elif len(candidates)>1:
            rec["status"]="AMBIGUOUS_OFFICIAL_MATCH"
        output.append(rec)
        time.sleep(0.1)
    summary={}
    for r in output: summary[r["status"]]=summary.get(r["status"],0)+1
    payload={"source_endpoint":RESULT,"fixed_bonus_endpoint":FIXED,"n":len(output),"status_counts":summary,
             "limitations":["Official endpoint history may not retain the original ticket-time snapshot.",
                            "Do not substitute Asian handicap or third-party odds for official hhad.",
                            "Any row without verifiable official goalLine remains unresolved."],"rows":output}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"n":len(output),"status_counts":summary,"out":str(OUT)},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
