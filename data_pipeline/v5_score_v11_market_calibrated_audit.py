#!/usr/bin/env python3
import csv,json,io,urllib.request,re
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/"model_validation/500_match_base_batch01.csv"
OUTCSV=ROOT/"model_validation/V5-SCORE_v1.1_market_data_coverage.csv"
OUTJSON=ROOT/"model_validation/V5-SCORE_v1.1_market_data_coverage.json"

LEAGUE_CODES={
"英超":[("2526","E0"),("2627","E0")],
"西甲":[("2526","SP1"),("2627","SP1")],
"德甲":[("2526","D1"),("2627","D1")],
"意甲":[("2526","I1"),("2627","I1")],
"法甲":[("2526","F1"),("2627","F1")],
"日职":[("2526","J1"),("2627","J1")],
}
# Football-Data documents opening/early odds separately from closing C-columns.
# K League is intentionally left unresolved rather than filled by another time point.
def norm(s):
    return re.sub(r"[^a-z0-9]","",(s or "").lower())

def load():
    with SAMPLE.open(encoding="utf-8-sig",newline="") as f:
        r=list(csv.DictReader(f))
    if len(r)!=500 or len({x["match_id"] for x in r})!=500:
        raise RuntimeError("Frozen 500 integrity failure")
    return r

def read_csv(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as x:
        raw=x.read().decode("latin1",errors="replace")
    return list(csv.DictReader(io.StringIO(raw)))

def val(row,*keys):
    for k in keys:
        v=row.get(k,"")
        if v not in ("",None):
            try:return float(str(v).strip())
            except: pass
    return None

def main():
    targets=load(); data=[]
    for league,codes in LEAGUE_CODES.items():
        for season,code in codes:
            url=f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
            try:
                rows=read_csv(url)
                for x in rows:
                    x["_league"]=league
                    data.append(x)
            except Exception:
                pass
    idx=defaultdict(list)
    for x in data:
        idx[(x.get("Date","")[:10],norm(x.get("HomeTeam")),norm(x.get("AwayTeam")))].append(x)
    out=[]
    for t in targets:
        key=(t["date"],norm(t["home"]),norm(t["away"]))
        c=idx.get(key,[])
        x=c[0] if len(c)==1 else None
        # Opening/non-C market fields. C-prefixed fields are intentionally excluded.
        rec={"sample_row":t["match_id"],"date":t["date"],"league":t["league"],"home":t["home"],"away":t["away"],
             "football_data_match":bool(x),"market_source":"Football-Data opening/early"}
        if x:
            rec.update({
              "euro_h":val(x,"B365H","AvgH","MaxH"),"euro_d":val(x,"B365D","AvgD","MaxD"),"euro_a":val(x,"B365A","AvgA","MaxA"),
              "ou25_over":val(x,"B365>2.5","Avg>2.5","P>2.5"),"ou25_under":val(x,"B365<2.5","Avg<2.5","P<2.5"),
              "ah_line":val(x,"AHh","BbAHh","AvgAHh"),"ah_home":val(x,"BbAvAHH","AvgAHH","MaxAHH"),
              "ah_away":val(x,"BbAvAHA","AvgAHA","MaxAHA"),
            })
        else:
            for k in ("euro_h","euro_d","euro_a","ou25_over","ou25_under","ah_line","ah_home","ah_away"): rec[k]=None
        rec["euro_complete"]=all(rec[k] is not None for k in ("euro_h","euro_d","euro_a"))
        rec["ou25_complete"]=all(rec[k] is not None for k in ("ou25_over","ou25_under"))
        rec["ah_complete"]=all(rec[k] is not None for k in ("ah_line","ah_home","ah_away"))
        # Existing DB Sporttery AH is handled later by the experiment runner; this audit
        # deliberately reports Football-Data market coverage separately.
        rec["all_three_complete"]=rec["euro_complete"] and rec["ou25_complete"] and rec["ah_complete"]
        out.append(rec)
    with OUTCSV.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=out[0].keys());w.writeheader();w.writerows(out)
    report={"sample_n":500,"football_data_rows":len(data),
            "football_data_match_n":sum(r["football_data_match"] for r in out),
            "euro_complete_n":sum(r["euro_complete"] for r in out),
            "ou25_complete_n":sum(r["ou25_complete"] for r in out),
            "ah_complete_n":sum(r["ah_complete"] for r in out),
            "all_three_complete_n":sum(r["all_three_complete"] for r in out),
            "unresolved_leagues":sorted(set(r["league"] for r in out if not r["football_data_match"])),
            "note":"No v1.1 score calibration is run until market coverage is audited; no closing C-columns are used."}
    OUTJSON.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
if __name__=="__main__":main()
