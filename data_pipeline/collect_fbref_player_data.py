import hashlib, re, sqlite3, time
from datetime import datetime
from io import StringIO
from pathlib import Path
import pandas as pd
import requests
from lxml import html as lhtml

DB="football_model_database.sqlite"; START="2026-01-01"; END="2026-09-20"
CACHE=Path(".cache/fbref"); CACHE.mkdir(parents=True,exist_ok=True)
LEAGUES={"9":"ENG-Premier League","12":"ESP-La Liga","20":"GER-Bundesliga","11":"ITA-Serie A","13":"FRA-Ligue 1"}
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0 (compatible; football-model-data/1.0)","Accept-Language":"en-US,en;q=0.9"})

def norm(s): return re.sub(r"[^a-z0-9]","",str(s).lower())
def get(url,name,sleep=1.2):
    p=CACHE/name
    if p.exists(): return p.read_text(encoding="utf-8",errors="ignore")
    for i in range(4):
        try:
            r=S.get(url,timeout=30)
            if r.status_code==200 and len(r.text)>1000:
                p.write_text(r.text,encoding="utf-8"); time.sleep(sleep); return r.text
            time.sleep(8*(i+1) if r.status_code in (403,429) else 2*(i+1))
        except Exception: time.sleep(3*(i+1))
    return None

def existing_matches(con):
    return [(mid,ko[:10],norm(h),norm(a)) for mid,ko,h,a in con.execute(
        "SELECT match_id,kickoff,home_team,away_team FROM matches WHERE kickoff>=? AND kickoff<=?",
        (START,END+"T23:59:59")).fetchall()]

def match_links(s):
    s=s.replace("<!--","").replace("-->","")
    return list(dict.fromkeys("https://fbref.com"+m.group(1) for m in re.finditer(r'href="(/en/matches/[^"]+)"',s)))

def match_for_report(text,matches,date):
    n=norm(text); hits=[mid for mid,d,h,a in matches if d==date and h in n and a in n]
    return hits[0] if len(set(hits))==1 else None

def pid(name): return "fbref:"+hashlib.sha1(norm(name).encode()).hexdigest()[:16]
def num(v):
    try: return float(str(v).replace("—","0").replace("-","0").strip() or 0)
    except: return 0.0

def parse_report(rh,mid,con):
    root=lhtml.fromstring(rh.replace("<!--","").replace("-->",""))
    tables=root.xpath("//table[starts-with(@id,'stats_') and contains(@id,'_summary')]")
    inserted=0
    for idx,t in enumerate(tables[:2]):
        try: dfs=pd.read_html(StringIO(lhtml.tostring(t,encoding="unicode")))
        except Exception: continue
        if not dfs: continue
        d=dfs[0]
        if isinstance(d.columns,pd.MultiIndex): d.columns=[c[-1] for c in d.columns]
        if "Player" not in d.columns or "Min" not in d.columns: continue
        side="home" if idx==0 else "away"
        for _,r in d.iterrows():
            name=str(r.get("Player","")).strip()
            if not name or name.lower()=="player" or re.match(r"^\d+ Players$",name): continue
            minutes=num(r.get("Min",0))
            con.execute("""INSERT OR REPLACE INTO player_match_stats
              (match_id,team_side,player_id,player_name,position,starter,minutes_played,rating,
               goals,assists,xg,xa,shots,key_passes,tackles,interceptions,clearances,data_source,observed_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (mid,side,pid(name),name,str(r.get("Pos","") or ""),1 if minutes>=45 else 0,minutes,None,
               num(r.get("Gls",0)),num(r.get("Ast",0)),num(r.get("xG",0)),num(r.get("xAG",0)),
               num(r.get("Sh",0)),num(r.get("KP",0)),num(r.get("Tkl",0)),num(r.get("Int",0)),
               num(r.get("Clr",0)),"FBref free fallback",datetime.utcnow().isoformat(timespec="seconds")))
            inserted+=1
    return inserted

def main():
    con=sqlite3.connect(DB); matches=existing_matches(con)
    total=mapped=reports=failures=0; seen=set()
    for comp_id in LEAGUES:
        for season in ("2025","2026"):
            season_span=f"{season}-{int(season)+1}"
            sh=get(f"https://fbref.com/en/comps/{comp_id}/{season_span}/schedule/{season_span}-Scores-and-Fixtures",
                   Path(f"schedule_{comp_id}_{season}.html"),1.5)
            if not sh: failures+=1; continue
            for report in match_links(sh):
                if report in seen: continue
                seen.add(report)
                key=re.sub(r"[^A-Za-z0-9]","_",report.split("/")[-1])
                rh=get(report,Path("report_"+key+".html"),1.0)
                if not rh: failures+=1; continue
                m=re.search(r"(20\d{2}-\d{2}-\d{2})",rh)
                if not m: continue
                date=m.group(1)
                if not (START<=date<=END): continue
                mid=match_for_report(rh,matches,date)
                if not mid: continue
                n=parse_report(rh,mid,con); reports+=1
                if n: mapped+=1; total+=n
                if reports%20==0:
                    con.commit(); print({"reports":reports,"mapped_matches":mapped,"player_rows":total},flush=True)
    con.commit(); con.close()
    print({"provider":"FBref_free","reports_seen":reports,"mapped_matches":mapped,"player_rows":total,"failures":failures},flush=True)

if __name__=="__main__": main()
