import json, os, re, sqlite3, math
from datetime import datetime
import requests

DB = "football_model_database.sqlite"
OUT = os.environ.get("SITE_BACKTEST_OUT", "data/jc_daily_report_backtest_500.json")
N = int(os.environ.get("SITE_BACKTEST_N", "500"))
REPO = "chinjiaqing/jc-daily-report"
RAW = "https://raw.githubusercontent.com/chinjiaqing/jc-daily-report/main/"
API = f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"

def clean(s):
    return re.sub(r"\\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()

def outcome(h, a):
    return "H" if h > a else "D" if h == a else "A"

def parse_cards(html, source_date):
    cards = re.findall(r'<div class="match-card[^>]*>(.*?)</div>\\s*(?=<div class="match-card|</main>)', html, re.S)
    out=[]
    for b in cards:
        if "has-sig" not in b:
            continue
        tm=re.search(r'<span class="font-mono">([^<]+)</span>', b)
        teams=re.search(r'<span class="text-\\[15px\\] font-bold[^>]*>(.*?)</span>', b, re.S)
        score=re.search(r'<span class="tnum text-sm font-black[^>]*>(\\d+)\\s*:\\s*(\\d+)</span>', b)
        sigs=re.findall(r'<span class="font-semibold text-emerald-700">([^<]+)</span>', b)
        if not tm or not teams or not score or not sigs:
            continue
        t=clean(tm.group(1))
        ts=clean(teams.group(1))
        parts=re.split(r"\\s+vs\\s+", ts, maxsplit=1)
        if len(parts)!=2:
            continue
        h,a=parts
        sh,sa=int(score.group(1)),int(score.group(2))
        text=" | ".join(clean(x) for x in sigs)
        out.append({"date":source_date,"kickoff":t,"home":h,"away":a,"score":[sh,sa],"actual":outcome(sh,sa),"signal_text":text})
    return out

def signal_set(text):
    s=text.replace(" ", "")
    if "主不败" in s or "主队不败" in s:
        return {"H","D"}
    if "客不败" in s or "客队不败" in s:
        return {"D","A"}
    if "主胜" in s:
        return {"H"}
    if "客胜" in s:
        return {"A"}
    if "平局" in s or re.search(r"(?<!不)平(?!手)", s):
        return {"D"}
    return set()

def canonical(s):
    s=s.lower()
    s=re.sub(r"[\\s·・.。'’‘()（）\-—_/]+","",s)
    return s

def main():
    tree=requests.get(API,timeout=30).json()
    files=[x["path"] for x in tree.get("tree",[]) if x["path"].startswith("daily/") and x["path"].endswith(".html")]
    all_rows=[]
    for path in sorted(files):
        d=path.split("/")[-1].replace(".html","")
        try:
            html=requests.get(RAW+path,timeout=30).text
            all_rows.extend(parse_cards(html,d))
        except Exception as e:
            print("FETCH_FAIL",path,str(e))
    # newest first; use only completed cards carrying a readable signal
    all_rows=sorted(all_rows,key=lambda x:(x["date"],x["kickoff"]),reverse=True)
    rows=all_rows[:N]
    exact=[r for r in rows if len(signal_set(r["signal_text"]))==1]
    double=[r for r in rows if len(signal_set(r["signal_text"]))==2]
    exact_correct=sum(next(iter(signal_set(r["signal_text"])))==r["actual"] for r in exact)
    double_hit=sum(r["actual"] in signal_set(r["signal_text"]) for r in double)
    allowed=[r for r in rows if signal_set(r["signal_text"])]
    allowed_hit=sum(r["actual"] in signal_set(r["signal_text"]) for r in allowed)

    # Match the external signals to the local finished-match table by date/time + normalized team names.
    con=sqlite3.connect(DB)
    local=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL""").fetchall()
    by_key={}
    for m in local:
        dt=m[1][:16].replace("T"," ")
        by_key[(dt,canonical(m[2]),canonical(m[3]))]=m
    matched=[]
    for r in rows:
        dt=r["date"]+" "+r["kickoff"][:5]
        m=by_key.get((dt,canonical(r["home"]),canonical(r["away"])))
        if m:
            matched.append((r,m))
    result={
      "definition":"500-match external-source backtest. Source signals come from the historical jc-daily-report HTML pages and are never fitted to the target results. The source score is used only as the historical outcome displayed by the source; model/database results are independently matched where possible.",
      "source_repo":REPO,
      "source_url":"https://github.com/chinjiaqing/jc-daily-report",
      "n_source_cards":len(all_rows),
      "n":len(rows),
      "exact_1x2":{"n":len(exact),"correct":exact_correct,"accuracy":round(exact_correct/len(exact),4) if exact else None},
      "double_chance":{"n":len(double),"hit":double_hit,"hit_rate":round(double_hit/len(double),4) if double else None},
      "all_directional_signals":{"n":len(allowed),"hit":allowed_hit,"hit_rate":round(allowed_hit/len(allowed),4) if allowed else None},
      "local_db_overlap":len(matched),
      "warning":"This is a descriptive external-source validation. No coefficient is fitted on these 500 matches and V4 is not changed by this run.",
      "sample":rows,
      "matched": [{"source":r,"local":{"match_id":m[0],"kickoff":m[1],"home":m[2],"away":m[3],"actual":outcome(m[4],m[5])}} for r,m in matched]
    }
    with open(OUT,"w",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ("sample","matched")},ensure_ascii=False))
    con.close()

if __name__=="__main__":
    main()
