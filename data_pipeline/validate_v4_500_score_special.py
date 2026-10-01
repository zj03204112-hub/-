#!/usr/bin/env python3
import csv, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "model_validation/500_match_V4_frozen_predictions.csv"
SAMPLE = ROOT / "model_validation/500_match_base_batch01.csv"
OUT = ROOT / "model_validation/500_match_V4_score_special_validation.csv"
AUDIT = ROOT / "model_validation/500_match_V4_score_special_validation.md"

ALIASES = {
    "曼彻斯特联":"曼联","曼彻斯特城":"曼城","托特纳姆热刺":"热刺","RB莱比锡":"RB莱比锡",
    "莱比锡红牛":"RB莱比锡","云达不来梅":"云达不莱梅","埃尔沃斯贝格":"埃弗斯贝格",
    "巴黎圣日耳曼":"巴黎圣日耳曼","巴黎圣日尔曼":"巴黎圣日耳曼","皇家贝蒂斯":"皇家贝蒂斯",
    "马德里竞技":"马德里竞技","皇家奥维耶多":"皇家奥维耶多","桑坦德竞技":"桑坦德竞技",
    "横滨水手":"横滨水手","FC东京":"FC东京","东京FC":"FC东京","清水心跳":"清水鼓动",
    "清水鼓动":"清水鼓动","大田市民":"大田","济州SK":"济州SK","仁川联":"仁川联",
    "全北现代":"全北","广岛三箭":"广岛","大阪钢巴":"大阪钢巴","大阪樱花":"大阪樱花",
    "神户胜利船":"神户","京都不死鸟":"京都","长崎成功丸":"长崎","水户蜀葵":"水户",
    "千叶市原":"千叶","冈山绿雉":"冈山","名古屋鲸八":"名古屋","柏太阳神":"柏太阳神",
    "川崎前锋":"川崎","浦和红钻":"浦和","东京绿茵":"东京绿茵","福冈黄蜂":"福冈",
    "马略卡":"马略卡","巴伦西亚":"瓦伦西亚","瓦伦西亚":"瓦伦西亚",
}
def norm(s):
    s = (s or "").strip().replace(" ","").replace("\u3000","")
    return ALIASES.get(s,s)

def cls_from_score(h,a):
    return "胜" if h>a else ("平" if h==a else "负")

def handicap_cls(h,a,handicap):
    #竞彩让球为整数：主队得分 + 让球数后与客队比较
    return cls_from_score(h + handicap, a)

def fetch(url):
    req = Request(url, headers={"User-Agent":"Mozilla/5.0 football-model/1.0"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8","ignore")
        except (URLError, HTTPError, TimeoutError):
            if attempt == 3: raise
            time.sleep(1.5 * (attempt+1))

def parse_page(html):
    # VIPC文本结构：赛事 001 时间 | 主vs客 2:1 (...)；下一行：胜 | 平 (-1) | ...
    rows=[]
    lines=[re.sub(r"\s+"," ",x).strip() for x in html.splitlines() if x.strip()]
    for i,line in enumerate(lines):
        m=re.search(r"^(.*?)\s+\d{3}\s+\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*\|\s*(.*?)vs(.*?)\s+(\d+)\s*:\s*(\d+)",line)
        if not m: continue
        league=m.group(1).strip()
        home=m.group(2).strip(); away=m.group(3).strip()
        hs=int(m.group(4)); aas=int(m.group(5))
        if i+1 >= len(lines): continue
        rline=lines[i+1]
        hm=re.search(r"([胜平负])\s*\|\s*([胜平负])\s*\(([+-]?\d+)\)",rline)
        if not hm: continue
        rows.append({
            "league":league,"home":home,"away":away,"home_score":hs,"away_score":aas,
            "actual_result_cn":hm.group(1),"handicap_result_cn":hm.group(2),
            "handicap":int(hm.group(3))
        })
    return rows

def read_csv(path):
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))

def main():
    pred=read_csv(BASE)
    sample=read_csv(SAMPLE)
    if len(pred)!=500 or len(sample)!=500:
        raise SystemExit(f"expected 500 rows, got predictions={len(pred)}, sample={len(sample)}")

    dates=sorted(set(r["date"] for r in sample))
    pages={}
    errors=[]
    for n,d in enumerate(dates,1):
        url=f"https://www.vipc.cn/results/jczq/{d.replace('-','')}"
        try:
            pages[d]=parse_page(fetch(url))
            print(f"[{n}/{len(dates)}] {d}: {len(pages[d])} parsed")
        except Exception as e:
            pages[d]=[]
            errors.append(f"{d}: {e}")
            print(f"[{n}/{len(dates)}] {d}: FETCH ERROR {e}")

    out=[]
    unresolved=[]
    for idx,(p,s) in enumerate(zip(pred,sample),1):
        # Directly match the already frozen sample identity; actual score is cross-checked against VIPC.
        target_date=s["date"]; h=norm(s["home"]); a=norm(s["away"])
        candidates=[x for x in pages.get(target_date,[]) if norm(x["home"])==h and norm(x["away"])==a]
        if not candidates:
            # Fallback: same teams and score in adjacent page captured under the date.
            candidates=[x for rows in pages.values() for x in rows if norm(x["home"])==h and norm(x["away"])==a and x["home_score"]==int(s["home_score"]) and x["away_score"]==int(s["away_score"])]
        if len(candidates)!=1:
            unresolved.append((idx,s["date"],s["home"],s["away"],len(candidates)))
            row={
                "场次":idx,"球队":f'{s["home"]} vs {s["away"]}',
                "预测第一比分":p["pred_score_1"],"预测第二比分":p["pred_score_2"],
                "预测胜负":{"H":"胜","D":"平","A":"负"}.get(p["pred_result"],p["pred_result"]),
                "竞彩让球盘口":"","预测让胜平负":"","实际比分":f'{s["home_score"]}-{s["away_score"]}',
                "实际胜负":{"H":"胜","D":"平","A":"负"}.get(p["actual_result"],p["actual_result"]),
                "实际让胜平负":"","核验状态":"未匹配竞彩历史页"
            }
            out.append(row); continue

        x=candidates[0]
        if x["home_score"]!=int(s["home_score"]) or x["away_score"]!=int(s["away_score"]):
            unresolved.append((idx,s["date"],s["home"],s["away"],"score_mismatch"))
            status="竞彩页比分不一致"
        else:
            status="已核验"
        try:
            p1h,p1a=map(int,p["pred_score_1"].split("-"))
            p2h,p2a=map(int,p["pred_score_2"].split("-"))
            ph=handicap_cls(p1h,p1a,x["handicap"])
            sh=handicap_cls(p2h,p2a,x["handicap"])
            pred_hand=f'{ph}/{sh}'
        except Exception:
            pred_hand=""
        out.append({
            "场次":idx,"球队":f'{s["home"]} vs {s["away"]}',
            "预测第一比分":p["pred_score_1"],"预测第二比分":p["pred_score_2"],
            "预测胜负":{"H":"胜","D":"平","A":"负"}.get(p["pred_result"],p["pred_result"]),
            "竞彩让球盘口":f'{x["handicap"]:+d}',
            "预测让胜平负":pred_hand,
            "实际比分":f'{s["home_score"]}-{s["away_score"]}',
            "实际胜负":{"H":"胜","D":"平","A":"负"}.get(p["actual_result"],p["actual_result"]),
            "实际让胜平负":x["handicap_result_cn"],
            "核验状态":status
        })

    fields=["场次","球队","预测第一比分","预测第二比分","预测胜负","竞彩让球盘口","预测让胜平负","实际比分","实际胜负","实际让胜平负","核验状态"]
    with OUT.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)

    ok=sum(r["核验状态"]=="已核验" for r in out)
    lines=[
        "# V4 500场比分专项验证",
        "",
        f"- 固定样本：500场",
        f"- 竞彩历史页来源：VIPC 竞彩足球开奖历史页（按日期抓取）",
        f"- 已核验：{ok}/500",
        f"- 未匹配：{500-ok}/500",
        "- 预测让胜平负：分别按第一比分、第二比分在实际竞彩让球盘口下计算。",
        "- 实际让胜平负：直接采用竞彩历史页公布的让球结果，不根据模型比分反推。",
        "",
        "## 未匹配场次",
    ]
    lines += [f"- {x}" for x in unresolved] if unresolved else ["- 无"]
    if errors:
        lines += ["","## 页面抓取错误"]+[f"- {x}" for x in errors]
    AUDIT.write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(f"DONE: {ok}/500 verified; output={OUT}")

if __name__=="__main__":
    main()
