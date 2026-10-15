#!/usr/bin/env python3
"""Backfill historical Sporttery 1X2 and handicap-1X2 odds for the frozen 448 sample.
Writes audit CSV/JSON; never invents timestamps or treats date-only captures as snapshots.
"""
import csv, json, re, time, unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/"model_validation/v52_jincai_20260901_20261013/v52_plus_nations_league_448_merged_backtest_20261013.csv"
OUT=ROOT/"model_validation/v52_jincai_20260901_20261013"
BASE="https://webapi.sporttery.cn/gateway/uniform/football"
HEADERS={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/145 Safari/537.36",
"Referer":"https://www.sporttery.cn/","Accept":"application/json, text/plain, */*"}
ALIASES={
"横滨水手":"横滨f水手","横浜fm":"横滨f水手","横浜fマリノス":"横滨f水手",
"fc东京":"东京fc","东京fc":"fc东京","川崎前锋":"川崎前锋","大阪钢巴":"大阪钢巴",
"Ｃ大阪":"大阪樱花","c大阪":"大阪樱花","大阪樱花":"大阪樱花",
"皇家社会":"皇家社会","real sociedad":"皇家社会","sociedad":"皇家社会",
"paris saint germain":"巴黎圣日耳曼","psg":"巴黎圣日耳曼",
"manchester city":"曼彻斯特城","man city":"曼彻斯特城",
"manchester united":"曼彻斯特联","man united":"曼彻斯特联",
"tottenham":"托特纳姆热刺","tottenham hotspur":"托特纳姆热刺",
"nottingham forest":"诺丁汉森林","wolves":"狼队","wolverhampton wanderers":"狼队",
"brighton":"布莱顿","brighton and hove albion":"布莱顿",
"bayern munich":"拜仁慕尼黑","bayern munchen":"拜仁慕尼黑",
"inter milan":"国际米兰","internazionale":"国际米兰",
"ac milan":"ac米兰","athletic bilbao":"毕尔巴鄂竞技",
"real madrid":"皇家马德里","barcelona":"巴塞罗那","atletico madrid":"马德里竞技",
"stuttgart":"斯图加特","fc koln":"科隆","koln":"科隆",
}
def norm(x):
    x=unicodedata.normalize("NFKC",str(x or "")).lower().strip()
    x=re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", x)
    return ALIASES.get(x, x)
def sim(a,b):
    a,b=norm(a),norm(b)
    if not a or not b:return 0
    if a==b:return 1
    if len(a)>3 and (a in b or b in a):return .9
    # no fuzzy guessing: unmatched teams must remain unresolved
    return 0
def get_json(s, url, params):
    r=s.get(url,params=params,timeout=25); r.raise_for_status()
    j=r.json()
    if j.get("success") is False: return {}
    return j.get("value") or {}
def date_matches(s,d):
    # Official results archive uses startDate/endDate.
    v=get_json(s,BASE+"/getUniformMatchResultV1.qry",{"startDate":d,"endDate":d})
    return v.get("matchResult") or []
def odds_history(s,mid):
    v=get_json(s,BASE+"/getFixedBonusV1.qry",{"clientCode":"3001","matchId":mid})
    return v.get("oddsHistory") or {}
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with SAMPLE.open(encoding="utf-8-sig",newline="") as f: sample=list(csv.DictReader(f))
    if len(sample)!=448: raise SystemExit(f"FROZEN_SAMPLE_FAIL expected=448 actual={len(sample)}")
    sess=requests.Session(); sess.headers.update(HEADERS)
    bydate={}
    for r in sample: bydate.setdefault(r["date"],[]).append(r)
    matches=[]; history=[]; status=Counter(); matchmap=[]
    for di,d in enumerate(sorted(bydate),1):
        try: archive=date_matches(sess,d)
        except Exception as e:
            status["date_api_error"]+=len(bydate[d]); print("DATE_API_ERROR",d,str(e)[:160],flush=True); continue
        used=set()
        for r in bydate[d]:
            cand=[]
            for x in archive:
                sid=str(x.get("matchId") or x.get("matchID") or "")
                if not sid or sid in used: continue
                home=x.get("homeTeamName") or x.get("homeName") or x.get("homeTeam") or ""
                away=x.get("awayTeamName") or x.get("awayName") or x.get("awayTeam") or ""
                score=sim(r["home_team"],home)+sim(r["away_team"],away)
                if score>=1.8: cand.append((score,sid,x,home,away))
            if not cand:
                status["no_official_match_id"]+=1
                matchmap.append({"match_id":r["match_id"],"date":d,"home_team":r["home_team"],"away_team":r["away_team"],"sporttery_match_id":"","mapping_status":"UNMATCHED"})
                continue
            cand.sort(key=lambda z:z[0],reverse=True)
            if len(cand)>1 and cand[0][0]==cand[1][0]:
                status["ambiguous_match_id"]+=1
                matchmap.append({"match_id":r["match_id"],"date":d,"home_team":r["home_team"],"away_team":r["away_team"],"sporttery_match_id":"","mapping_status":"AMBIGUOUS"})
                continue
            _,sid,x,home,away=cand[0]; used.add(sid); status["mapped"]+=1
            matchmap.append({"match_id":r["match_id"],"date":d,"home_team":r["home_team"],"away_team":r["away_team"],"sporttery_match_id":sid,"mapping_status":"MAPPED"})
            matches.append({"match_id":r["match_id"],"date":d,"home_team":r["home_team"],"away_team":r["away_team"],"sporttery_match_id":sid,"sporttery_home":home,"sporttery_away":away})
            try: oh=odds_history(sess,sid)
            except Exception as e:
                status["odds_api_error"]+=1; print("ODDS_API_ERROR",sid,str(e)[:120],flush=True); continue
            for market,key in [("1X2","hadList"),("SPORTTERY_HANDICAP_1X2","hhadList")]:
                arr=oh.get(key) or []
                if not arr: status["no_"+key]+=1
                for q in arr:
                    dt=str(q.get("updateDate") or "").strip()
                    tm=str(q.get("updateTime") or "").strip()
                    stamp=(dt+" "+tm).strip()
                    parsed=None
                    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y/%m/%d %H:%M:%S","%Y/%m/%d %H:%M"):
                        try: parsed=datetime.strptime(stamp,fmt).isoformat(sep=" "); break
                        except ValueError: pass
                    history.append({"match_id":r["match_id"],"date":d,"home_team":r["home_team"],"away_team":r["away_team"],
                      "sporttery_match_id":sid,"market":market,"update_date":dt,"update_time":tm,"timestamp_parsed":parsed or "",
                      "handicap_line":q.get("goalLine","") if market.startswith("SPORTTERY") else "",
                      "home_odds":q.get("h",""),"draw_odds":q.get("d",""),"away_odds":q.get("a",""),
                      "source":"Sporttery getFixedBonusV1 oddsHistory","time_quality":"TIMESTAMPED" if parsed else "UNPARSED_TIMESTAMP"})
            time.sleep(.12)
        print(json.dumps({"date":d,"dates":f"{di}/{len(bydate)}","matched":status["mapped"],"history_rows":len(history)},ensure_ascii=False),flush=True)
    def write_csv(path,rows):
        cols=list(dict.fromkeys(k for row in rows for k in row.keys()))
        with path.open("w",encoding="utf-8-sig",newline="") as f:
            w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
    write_csv(OUT/"v52_448_sporttery_match_mapping.csv",matchmap)
    write_csv(OUT/"v52_448_historical_odds.csv",history)
    report={"sample_size":448,"mapped_matches":status["mapped"],"unmapped_matches":status["no_official_match_id"],
      "ambiguous_matches":status["ambiguous_match_id"],"date_api_errors":status["date_api_error"],
      "odds_api_errors":status["odds_api_error"],"historical_odds_rows":len(history),
      "market_rows_by_type":dict(Counter(x["market"] for x in history)),
      "timestamped_rows":sum(bool(x["timestamp_parsed"]) for x in history),
      "status_counts":dict(status),
      "important_note":"Sporttery hhadList is lottery handicap 1X2 odds, not Asian handicap market odds. Historical odds snapshots are not automatically T-12 eligible; use parsed update timestamp against actual kickoff. No missing odds are imputed."}
    (OUT/"v52_448_historical_odds_backfill_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("FINAL_REPORT",json.dumps(report,ensure_ascii=False),flush=True)
if __name__=="__main__": main()
