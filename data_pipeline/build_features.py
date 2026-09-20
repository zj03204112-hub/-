import json, sqlite3
from datetime import datetime
DB="football_model_database.sqlite"

def points(gf,ga): return 3 if gf>ga else 1 if gf==ga else 0

def history(con,team,before):
    rows=con.execute("""SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
      AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
      ORDER BY m.kickoff DESC LIMIT 12""",(before,team,team)).fetchall()
    out=[]
    for k,h,a,fh,fa in rows:
        gf,ga=(fh,fa) if h==team else (fa,fh)
        out.append({"kickoff":k,"date":k[:10],"gf":gf,"ga":ga,"pts":points(gf,ga),"opp":a if h==team else h})
    return out

def strength(con,team,before):
    x=history(con,team,before)[:10]
    return sum(v["pts"] for v in x)/len(x) if x else 0.0

def opponent_adjusted_strength(con,team,before):
    x=history(con,team,before)[:10]
    if not x: return 0.0
    weights=[1.0/(1.0+i*0.12) for i in range(len(x))]
    vals=[]
    for i,v in enumerate(x):
        os=strength(con,v["opp"],before)
        vals.append((v["pts"]*(0.75+0.25*max(0.5,min(1.5,os/1.5))),weights[i]))
    return sum(v*w for v,w in vals)/sum(weights)

def next_fixture(con,team,after):
    return con.execute("""SELECT kickoff,home_team,away_team FROM matches
      WHERE kickoff > ? AND (home_team=? OR away_team=?)
      ORDER BY kickoff LIMIT 1""",(after,team,team)).fetchone()

def main():
    con=sqlite3.connect(DB)
    matches=con.execute("""SELECT match_id,kickoff,home_team,away_team
      FROM matches WHERE status='finished' ORDER BY kickoff,match_id""").fetchall()
    con.execute("DELETE FROM team_features"); con.execute("DELETE FROM schedule_intent")
    for mid,kickoff,home,away in matches:
        for side,team in (("home",home),("away",away)):
            hist=history(con,team,kickoff)
            pack=lambda n: json.dumps(hist[:n],ensure_ascii=False,separators=(",",":"))
            n=max(1,min(10,len(hist)))
            gf=sum(x["gf"] for x in hist[:10])/n; ga=sum(x["ga"] for x in hist[:10])/n
            opps=[x["opp"] for x in hist[:10]]
            os=sum(opponent_adjusted_strength(con,o,kickoff) for o in opps)/len(opps) if opps else 0.0
            con.execute("""INSERT INTO team_features
              (match_id,team_side,last3,last5,last10,goals_for,goals_against,xg_for,xg_against,opponent_strength)
              VALUES (?,?,?,?,?,?,?,?,?,?)""",(mid,side,pack(3),pack(5),pack(10),gf,ga,None,None,os))
            nxt=next_fixture(con,team,kickoff)
            if not nxt: continue
            nk,nh,na=nxt; opp=na if nh==team else nh
            try:
                rest=max(0,(datetime.fromisoformat(str(nk)[:19])-datetime.fromisoformat(str(kickoff)[:19])).total_seconds()/86400)
            except Exception: rest=0.0
            os2=strength(con,opp,kickoff)
            risk=0.75 if rest<=3 else 0.45 if rest<=4 else 0.2
            if os2>=2.0: risk=min(1.0,risk+0.15)
            importance="high" if os2>=2.0 else ("medium" if rest<=4 else "normal")
            adj=-0.10 if risk>=0.7 else (-0.05 if risk>=0.4 else 0.0)
            con.execute("""INSERT INTO schedule_intent
              (match_id,team_side,next_opponent,next_match_time,rest_days,next_match_importance,rotation_risk,motivation_adjustment)
              VALUES (?,?,?,?,?,?,?,?)""",(mid,side,opp,nk,round(rest,2),importance,risk,adj))
    con.commit()
    print("feature rows:",con.execute("SELECT COUNT(*) FROM team_features").fetchone()[0])
    print("schedule rows:",con.execute("SELECT COUNT(*) FROM schedule_intent").fetchone()[0])
    con.close()

if __name__=="__main__": main()
