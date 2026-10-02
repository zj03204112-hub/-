#!/usr/bin/env python3
import csv,json,urllib.parse,urllib.request,urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MAP=ROOT/"model_validation/500_match_to_db_match_id_mapping.csv"
OUT=ROOT/"model_validation/sporttery_matchid_smoke5.json"
BASE="https://webapi.sporttery.cn/gateway"
LIST=BASE+"/uniform/fb/getMatchDataPageListV1.qry"
FIXED=BASE+"/uniform/football/getFixedBonusV1.qry"
HEAD={"User-Agent":"Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/134 Mobile Safari/537.36","Referer":"https://m.sporttery.cn/mjc/zqsj/?tab=result","Accept":"application/json,text/plain,*/*"}

def get(url,params):
    u=url+"?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(u,headers=HEAD)
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.load(r),u

def norm(s):
    return "".join(c.lower() for c in (s or "") if c.isalnum() or "\u4e00"<=c<="\u9fff")

def score(m):
    s=m.get("sectionsNo999") or ""
    import re
    x=re.findall(r"\d+",str(s))
    return (int(x[-2]),int(x[-1])) if len(x)>=2 else (None,None)

with MAP.open(encoding="utf-8-sig") as f: rows=list(csv.DictReader(f))
# deliberately use 5 different dates and competitions for smoke validation
targets=[rows[i] for i in [0,2,4,19,49]]
out=[]
for t in targets:
    d=t["db_kickoff"][:10]
    rec={"sample_row":t["sample_row"],"date":d,"home":t["db_home"],"away":t["db_away"],"db_score":f'{t["db_home_score"]}-{t["db_away_score"]}'}
    try:
        j,u=get(LIST,{"method":"result","matchDate":d})
        matches=[m for g in (j.get("value",{}).get("matchInfoList") or []) for m in (g.get("subMatchList") or [])]
        cand=[]
        for m in matches:
            mh=norm(m.get("allHomeTeam") or m.get("homeTeam"))
            ma=norm(m.get("allAwayTeam") or m.get("awayTeam"))
            hs,aw=score(m)
            if mh==norm(t["db_home"]) and ma==norm(t["db_away"]): cand.append(m)
            elif hs==int(t["db_home_score"]) and aw==int(t["db_away_score"]) and mh==norm(t["db_home"]) and ma==norm(t["db_away"]): cand.append(m)
        rec["list_count"]=len(matches); rec["candidate_count"]=len(cand)
        if not cand: rec["status"]="NO_MATCHID"; out.append(rec); continue
        m=cand[0]; mid=str(m.get("matchId"))
        rec["sporttery_match_id"]=mid; rec["sporttery_home"]=m.get("allHomeTeam") or m.get("homeTeam"); rec["sporttery_away"]=m.get("allAwayTeam") or m.get("awayTeam"); rec["sporttery_score"]=f"{score(m)[0]}-{score(m)[1]}"
        j2,u2=get(FIXED,{"clientCode":"3001","matchId":mid})
        v=j2.get("value") or {}; oh=v.get("oddsHistory") or {}; hh=oh.get("hhadList") or []
        rec["hhad_count"]=len(hh); rec["goal_lines"]=[x.get("goalLine") for x in hh if x.get("goalLine") not in (None,"")]
        rec["match_result_list_present"]=bool(v.get("matchResultList"))
        rec["status"]="OK" if hh else "NO_HHAD"
    except urllib.error.HTTPError as e:
        rec["status"]=f"HTTP_{e.code}"; rec["error"]=str(e)
    except Exception as e:
        rec["status"]="ERROR"; rec["error"]=str(e)
    out.append(rec)

report={"target_count":len(targets),"ok":sum(x["status"]=="OK" for x in out),"results":out}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
if report["ok"]<len(targets): raise SystemExit(2)
