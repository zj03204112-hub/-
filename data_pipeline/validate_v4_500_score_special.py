#!/usr/bin/env python3
import csv, json, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"model_validation/500_match_V4_frozen_predictions.csv"
SAMPLE=ROOT/"model_validation/500_match_base_batch01.csv"
OUT=ROOT/"model_validation/500_match_V4_score_special_validation.csv"
AUDIT=ROOT/"model_validation/500_match_V4_score_special_validation.md"
SPORTTERY="https://webapi.sporttery.cn/gateway/jc/football/getMatchResultV1.qry"
VIPC="https://www.vipc.cn/results/jczq/{}"

ALIASES={"曼彻斯特联":"曼联","曼彻斯特城":"曼城","托特纳姆热刺":"热刺","莱比锡红牛":"RB莱比锡","云达不来梅":"云达不莱梅","埃尔沃斯贝格":"埃弗斯贝格","巴黎圣日尔曼":"巴黎圣日耳曼","巴伦西亚":"瓦伦西亚","东京FC":"FC东京","清水心跳":"清水鼓动","大田市民":"大田","全北现代":"全北","广岛三箭":"广岛","神户胜利船":"神户","京都不死鸟":"京都","长崎成功丸":"长崎","水户蜀葵":"水户","千叶市原":"千叶","冈山绿雉":"冈山","名古屋鲸八":"名古屋","川崎前锋":"川崎","浦和红钻":"浦和","福冈黄蜂":"福冈"}

def norm(s):
    s=re.sub(r"[\\s\\u3000\\-·.'’]", "", str(s or "")).lower()
    return ALIASES.get(s,s)

def cls(h,a): return "胜" if h>a else ("平" if h==a else "负")
def handicap_cls(h,a,g): return cls(h+g,a)

def fetch(url,params=None):
    u=url+("?" + urlencode(params) if params else "")
    req=Request(u,headers={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/145.0 Safari/537.36","Referer":"https://www.sporttery.cn/","Accept":"application/json, text/plain, */*"})
    last=None
    for i in range(4):
        try:
            with urlopen(req,timeout=30) as r: return r.read().decode("utf-8","ignore")
        except Exception as e:
            last=e
            if i<3: time.sleep(1.5*(i+1))
    raise last

def parse_score(s):
    if not s: return None
    m=re.search(r"(\\d+)\\s*[:\\-]\\s*(\\d+)",str(s))
    return (int(m.group(1)),int(m.group(2))) if m else None

def parse_sporttery(text):
    data=json.loads(text)
    value=data.get("value") or {}
    raw=value.get("matchResult") or []
    rows=[]
    for d in raw:
        home=d.get("homeTeam") or d.get("allHomeTeam")
        away=d.get("awayTeam") or d.get("allAwayTeam")
        score=parse_score(d.get("sectionsNo999") or d.get("fullScore") or d.get("score"))
        goal=d.get("goalLine")
        if not home or not away or not score or goal in ("",None): continue
        try: goal=int(float(str(goal).replace("+","")))
        except Exception: continue
        rows.append({
            "match_id":str(d.get("matchId") or ""),
            "match_num":str(d.get("matchNumStr") or d.get("matchNum") or ""),
            "match_date":str(d.get("matchDate") or ""),
            "home":str(home),"away":str(away),
            "home_score":score[0],"away_score":score[1],
            "handicap":goal,
            "actual_result_cn":cls(score[0],score[1]),
            "handicap_result_cn":handicap_cls(score[0],score[1],goal)
        })
    return rows

def parse_vipc(html):
    rows=[]; lines=[re.sub(r"\\s+"," ",x).strip() for x in html.splitlines() if x.strip()]
    for i,line in enumerate(lines):
        m=re.search(r"^(.*?)\\s+\\d{3}\\s+\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2}\\s*\\|\\s*(.*?)vs(.*?)\\s+(\\d+)\\s*:\\s*(\\d+)",line)
        if not m or i+1>=len(lines): continue
        hm=re.search(r"([胜平负])\\s*\\|\\s*([胜平负])\\s*\\(([+-]?\\d+)\\)",lines[i+1])
        if hm: rows.append({"home":m.group(2).strip(),"away":m.group(3).strip(),"home_score":int(m.group(4)),"away_score":int(m.group(5)),"handicap":int(hm.group(3)),"actual_result_cn":hm.group(1),"handicap_result_cn":hm.group(2)})
    return rows


def read_csv(p):
    with p.open("r",encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))
def find(rows,s):
    h,a=norm(s["home"]),norm(s["away"]); hs,aa=int(s["home_score"]),int(s["away_score"])
    target=s["date"]
    q=[x for x in rows if str(x.get("match_date",""))[:10]==target and norm(x["home"])==h and norm(x["away"])==a and x["home_score"]==hs and x["away_score"]==aa]
    if len(q)==1:return q[0]
    q=[x for x in rows if norm(x["home"])==h and norm(x["away"])==a and x["home_score"]==hs and x["away_score"]==aa]
    if len(q)==1:return q[0]
    q=[x for x in rows if str(x.get("match_date",""))[:10]==target and norm(x["home"])==h and norm(x["away"])==a]
    return q[0] if len(q)==1 else None

def main():
    pred,sample=read_csv(BASE),read_csv(SAMPLE)
    if len(pred)!=500 or len(sample)!=500: raise SystemExit("500-row integrity check failed")
    dates=sorted({r["date"] for r in sample}); pages={}; errors=[]; source={"Sporttery":0,"VIPC":0}
    for i,d in enumerate(dates,1):
        rows=[]
        try:
            from datetime import datetime,timedelta
            dt=datetime.strptime(d,"%Y-%m-%d").date()
            begin=(dt-timedelta(days=1)).isoformat()
            end=(dt+timedelta(days=1)).isoformat()
            rows=parse_sporttery(fetch(SPORTTERY,{"matchPage":"1","matchBeginDate":begin,"matchEndDate":end,"leagueId":"","pageSize":"200","pageNo":"1","isFix":"0","pcOrWap":"1"}))
            source["Sporttery"]+=len(rows); print(f"[{i}/{len(dates)}] {d}: Sporttery {len(rows)} (query {begin}~{end})")
        except Exception as e:
            errors.append(f"Sporttery {d}: {e}"); print(f"[{i}/{len(dates)}] {d}: Sporttery ERROR {e}")
        if not rows:
            try:
                rows=parse_vipc(fetch(VIPC.format(d.replace("-",""))))
                source["VIPC"]+=len(rows); print(f"[{i}/{len(dates)}] {d}: VIPC fallback {len(rows)}")
            except Exception as e:
                errors.append(f"VIPC {d}: {e}"); print(f"[{i}/{len(dates)}] {d}: VIPC ERROR {e}")
        pages[d]=rows
    out=[]; bad=[]
    for idx,(p,s) in enumerate(zip(pred,sample),1):
        x=find(pages.get(s["date"],[]),s)
        pr={"H":"胜","D":"平","A":"负"}.get(p["pred_result"],p["pred_result"])
        ar={"H":"胜","D":"平","A":"负"}.get(p["actual_result"],p["actual_result"])
        if not x:
            bad.append(idx); out.append({"场次":idx,"球队":f'{s["home"]} vs {s["away"]}',"预测第一比分":p["pred_score_1"],"预测第二比分":p["pred_score_2"],"预测胜负":pr,"竞彩让球盘口":"","预测让胜平负":"","实际比分":f'{s["home_score"]}-{s["away_score"]}',"实际胜负":ar,"实际让胜平负":"","核验状态":"未匹配竞彩历史数据"}); continue
        p1h,p1a=map(int,p["pred_score_1"].split("-")); p2h,p2a=map(int,p["pred_score_2"].split("-"))
        ph,sh=handicap_cls(p1h,p1a,x["handicap"]),handicap_cls(p2h,p2a,x["handicap"])
        status="已核验" if x["home_score"]==int(s["home_score"]) and x["away_score"]==int(s["away_score"]) else "竞彩比分不一致"
        if status!="已核验": bad.append(idx)
        out.append({"场次":idx,"球队":f'{s["home"]} vs {s["away"]}',"预测第一比分":p["pred_score_1"],"预测第二比分":p["pred_score_2"],"预测胜负":pr,"竞彩让球盘口":f'{x["handicap"]:+d}',"预测让胜平负":f"第一:{ph} / 第二:{sh}","实际比分":f'{s["home_score"]}-{s["away_score"]}',"实际胜负":ar,"实际让胜平负":x["handicap_result_cn"],"核验状态":status})
    fields=["场次","球队","预测第一比分","预测第二比分","预测胜负","竞彩让球盘口","预测让胜平负","实际比分","实际胜负","实际让胜平负","核验状态"]
    with OUT.open("w",encoding="utf-8-sig",newline="") as f: w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
    ok=sum(r["核验状态"]=="已核验" for r in out); hn=sum(bool(r["竞彩让球盘口"]) for r in out)
    AUDIT.write_text("\n".join(["# V4 500场比分 + 竞彩让球专项验证","",f"- 固定样本：500场",f"- Sporttery成功抓取记录：{source['Sporttery']}",f"- VIPC备用抓取记录：{source['VIPC']}",f"- 比分/盘口核验成功：{ok}/500",f"- 竞彩让球盘口取得：{hn}/500",f"- 未核验/不一致：{len(bad)}/500","- 预测让胜平负按第一、第二预测比分分别叠加官方让球数计算。","- 实际让胜平负优先使用官方 hhad；无 hhad 时按官方 goalLine + 官方最终比分计算。","", "未核验场次：", ",".join(map(str,bad)) if bad else "无"])+"\n",encoding="utf-8")
    print(f"DONE verified={ok}/500 handicap={hn}/500 bad={len(bad)}")

if __name__=="__main__": main()
