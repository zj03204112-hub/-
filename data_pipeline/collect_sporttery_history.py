import os, sqlite3, time, requests
from datetime import date, timedelta

DB="football_model_database.sqlite"
START=date.fromisoformat(os.getenv("SPORTTERY_START","2026-01-01"))
END=date.fromisoformat(os.getenv("SPORTTERY_END","2026-09-20"))
URL="https://webapi.sporttery.cn/gateway/jc/football/getMatchResultV1.qry"
HEADERS={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
         "Referer":"https://m.sporttery.cn/","Accept":"application/json,text/plain,*/*",
         "Accept-Language":"zh-CN,zh;q=0.9"}

LEAGUE_KEYS={
 "EPL":["英超","英格兰超级联赛","premier league"],
 "LALIGA":["西甲","西班牙甲级联赛","la liga"],
 "BUNDESLIGA":["德甲","德国甲级联赛","bundesliga"],
 "SERIEA":["意甲","意大利甲级联赛","serie a"],
 "LIGUE1":["法甲","法国甲级联赛","ligue 1"],
 "KLEAGUE1":["韩职","韩国职业联赛","k league 1","k联赛"],
 "J1":["日职","日本职业联赛","j1 league","日本j联赛"],
}

def comp_for(name):
    x=str(name or "").lower()
    for code,keys in LEAGUE_KEYS.items():
        if any(k.lower() in x for k in keys): return code
    return None

def rows_for_day(sess, d):
    out=[]; page=1
    while True:
        p={"matchPage":1,"matchBeginDate":d.isoformat(),"matchEndDate":d.isoformat(),
           "leagueId":"","pageSize":200,"pageNo":page,"isFix":0,"pcOrWap":1}
        r=sess.get(URL,params=p,timeout=12)
        if r.status_code in (403, 429, 567):
            raise RuntimeError(f"Sporttery access blocked ({r.status_code})")
        r.raise_for_status()
        payload=r.json(); value=payload.get("value") or {}
        batch=value.get("matchResult") or []
        out.extend(batch)
        total=int(value.get("total") or 0)
        if len(out)>=total or not batch: break
        page+=1
        time.sleep(0.25)
    return out

def find_match(con, code, d, fh, fa):
    cid=con.execute("SELECT competition_id FROM competitions WHERE competition_code=?",(code,)).fetchone()
    if not cid: return None
    rows=con.execute("""SELECT m.match_id,m.home_team,m.away_team
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.competition_id=? AND substr(m.kickoff,1,10)=?
          AND r.ft_home=? AND r.ft_away=?""",(cid[0],d,fh,fa)).fetchall()
    return rows[0][0] if len(rows)==1 else None

def main():
    con=sqlite3.connect(DB); sess=requests.Session(); sess.headers.update(HEADERS)
    inserted=0; scanned=0; skipped=0
    d=START
    while d<=END:
        try:
            for x in rows_for_day(sess,d):
                code=comp_for(x.get("leagueName") or x.get("leagueNameAbbr"))
                if not code: continue
                score=str(x.get("sectionsNo999") or "")
                import re
                m=re.search(r"(\d+)\s*[:：-]\s*(\d+)",score)
                if not m: continue
                fh,fa=int(m.group(1)),int(m.group(2))
                mid=find_match(con,code,d.isoformat(),fh,fa)
                scanned+=1
                if not mid: skipped+=1; continue
                h=float(x["h"]) if str(x.get("h","")).replace(".","",1).isdigit() else None
                dr=float(x["d"]) if str(x.get("d","")).replace(".","",1).isdigit() else None
                a=float(x["a"]) if str(x.get("a","")).replace(".","",1).isdigit() else None
                gl=x.get("goalLine")
                if h is not None and dr is not None and a is not None:
                    con.execute("""INSERT INTO sporttery_market
                      (match_id,pool_code,handicap,home_value,draw_value,away_value,captured_at,source_status)
                      VALUES (?,?,?,?,?,?,?,?)""",(mid,"hhad",float(gl) if gl not in (None,"") else None,h,dr,a,
                      d.isoformat(),"sporttery_official_result_endpoint"))
                    inserted+=1
        except Exception as e:
            print("WARN",d,e)
            if "access blocked" in str(e).lower():
                print("Sporttery endpoint blocked; stopping collector without bypassing WAF.")
                break
        d+=timedelta(days=1)
        time.sleep(0.15)
    con.commit(); con.close()
    print("sporttery market rows:",inserted,"scanned:",scanned,"unmatched:",skipped)

if __name__=="__main__": main()
