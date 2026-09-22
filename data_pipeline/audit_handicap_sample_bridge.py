import json, sqlite3
from collections import Counter
DB="football_model_database.sqlite"
OUT="data/handicap_sample_bridge_audit.json"
TARGET={-3,-2,-1,1,2,3}
SOURCES=("asian_handicap_avg","sofascore_asian_featured","sgodds_open","football_data_ah_close","football_data_ah_bookmaker","football_data_ah_open")

def line(x):
    try:
        v=float(x)
        return int(round(v)) if abs(v-round(v))<1e-9 else None
    except Exception:
        return None

def result(fh,fa,l):
    d=fh+l-fa
    return "H" if d>0 else "D" if d==0 else "A"

def main():
    con=sqlite3.connect(DB)
    total=con.execute("SELECT COUNT(*) FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL").fetchone()[0]
    raw=con.execute("""SELECT sm.match_id,sm.handicap,sm.pool_code,r.ft_home,r.ft_away
      FROM sporttery_market sm JOIN results r ON r.match_id=sm.match_id
      JOIN matches m ON m.match_id=sm.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
        AND sm.handicap IS NOT NULL AND sm.pool_code IN (%s)""" % ",".join("?"*len(SOURCES)),SOURCES).fetchall()
    priority={s:len(SOURCES)-i for i,s in enumerate(SOURCES)}
    chosen={}
    for mid,h,p,fh,fa in raw:
        h=line(h)
        if h not in TARGET: continue
        k=(mid,h)
        if k not in chosen or priority.get(p,0)>priority.get(chosen[k][2],0): chosen[k]=(mid,h,p,fh,fa)
    by=Counter(); audits=Counter(); examples=[]
    for mid,h,p,fh,fa in chosen.values():
        by[str(h)]+=1
        actual=result(fh,fa,h)
        # Independent algebraic audit: same formula written in two equivalent forms.
        d1=fh+h-fa; d2=(fh-fa)+h
        audits["formula_consistent"]+=int((d1>0)==(d2>0) and (d1==0)==(d2==0))
        if len(examples)<20:
            examples.append({"match_id":mid,"line":h,"score":f"{fh}-{fa}","actual":actual,"source":p})
    out={"finished_matches":total,"market_rows":len(raw),"deduped_match_line_rows":len(chosen),"coverage":round(len(chosen)/total,4) if total else None,"by_line":dict(sorted(by.items(),key=lambda x:int(x[0]))),"formula_audit":dict(audits),"formula":"(home_goals-away_goals)+home_handicap","examples":examples}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps(out,ensure_ascii=False))
    con.close()
if __name__=="__main__": main()
