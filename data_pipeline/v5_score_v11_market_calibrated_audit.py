#!/usr/bin/env python3
import csv,json,io,urllib.request,re
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/"model_validation/500_match_base_batch01.csv"
OUTCSV=ROOT/"model_validation/V5-SCORE_v1.1_market_data_coverage.csv"
OUTJSON=ROOT/"model_validation/V5-SCORE_v1.1_market_data_coverage.json"
PAGE="https://sgodds.com/football/data"

def norm(s): return re.sub(r"[^a-z0-9]","",(s or "").lower())
def num(v):
    try:
        x=float(str(v).strip())
        return x if x==x else None
    except: return None
def load():
    with SAMPLE.open(encoding="utf-8-sig",newline="") as f:r=list(csv.DictReader(f))
    if len(r)!=500 or len({x["match_id"] for x in r})!=500: raise RuntimeError("Frozen 500 integrity failure")
    return r
def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=60) as x:return x.read()
def csv_rows(url):
    raw=get(url).decode("utf-8-sig",errors="replace")
    return list(csv.DictReader(io.StringIO(raw)))
def main():
    targets=load()
    html=get(PAGE).decode("utf-8",errors="replace")
    links=re.findall(r'href="(https://sgodds\.com/downloads/[^"]+\.csv)"',html)
    links=list(dict.fromkeys(links))
    rows=[]
    for url in links:
        try:
            for x in csv_rows(url):
                x["_source_url"]=url; rows.append(x)
        except Exception: pass
    idx=defaultdict(list)
    for x in rows:
        # SGOdds files use date/team columns; tolerate common naming variants.
        d=(x.get("Date") or x.get("date") or x.get("MatchDate") or "")[:10]
        h=x.get("HomeTeam") or x.get("Home") or x.get("home") or ""
        a=x.get("AwayTeam") or x.get("Away") or x.get("away") or ""
        idx[(d,norm(h),norm(a))].append(x)
    out=[]
    for t in targets:
        c=idx.get((t["date"],norm(t["home"]),norm(t["away"])),[])
        x=c[0] if len(c)==1 else None
        rec={"sample_row":t["match_id"],"date":t["date"],"league":t["league"],"home":t["home"],"away":t["away"],
             "sgodds_match":bool(x),"market_source":"SGOdds opening odds"}
        if x:
            rec.update({
              "euro_h":num(x.get("Ft1X2_01")),"euro_d":num(x.get("Ft1X2_02")),"euro_a":num(x.get("Ft1X2_03")),
              "ah_line":num(x.get("Ah_01_Hcap")),"ah_home":num(x.get("Ah_01")),"ah_away":num(x.get("Ah_02")),
              "ou_line":num(x.get("Ou_hcap")),"ou_over":num(x.get("Ou_01")),"ou_under":num(x.get("Ou_02")),
              "source_url":x.get("_source_url","")
            })
        else:
            for k in ("euro_h","euro_d","euro_a","ah_line","ah_home","ah_away","ou_line","ou_over","ou_under"):rec[k]=None
            rec["source_url"]=""
        rec["euro_complete"]=all(rec[k] is not None for k in ("euro_h","euro_d","euro_a"))
        rec["ah_complete"]=all(rec[k] is not None for k in ("ah_line","ah_home","ah_away"))
        rec["ou_complete"]=all(rec[k] is not None for k in ("ou_line","ou_over","ou_under"))
        rec["all_three_complete"]=rec["euro_complete"] and rec["ah_complete"] and rec["ou_complete"]
        rec["ou25"]=rec["ou_complete"] and abs(rec["ou_line"]-2.5)<1e-9
        rec["ou275"]=rec["ou_complete"] and abs(rec["ou_line"]-2.75)<1e-9
        out.append(rec)
    with OUTCSV.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=out[0].keys());w.writeheader();w.writerows(out)
    report={"sample_n":500,"sgodds_csv_count":len(links),"sgodds_rows":len(rows),
            "sgodds_match_n":sum(r["sgodds_match"] for r in out),
            "euro_complete_n":sum(r["euro_complete"] for r in out),
            "ah_complete_n":sum(r["ah_complete"] for r in out),
            "ou_complete_n":sum(r["ou_complete"] for r in out),
            "all_three_complete_n":sum(r["all_three_complete"] for r in out),
            "ou25_n":sum(r["ou25"] for r in out),"ou275_n":sum(r["ou275"] for r in out),
            "unresolved_leagues":sorted(set(r["league"] for r in out if not r["sgodds_match"])),
            "source_note":"SGOdds page identifies these as opening odds; fields include 1X2, Asian handicap and Total Goals Over/Under. No closing values are used."}
    OUTJSON.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
if __name__=="__main__":main()
