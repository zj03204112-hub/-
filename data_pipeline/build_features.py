import json, math, sqlite3
from datetime import datetime

DB = "football_model_database.sqlite"

def points(h, a):
    return 3 if h > a else 1 if h == a else 0

def team_history(conn, team, before):
    rows = conn.execute("""
        SELECT m.kickoff, m.home_team, m.away_team, r.ft_home, r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
          AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
        ORDER BY m.kickoff DESC
    """, (before, team, team)).fetchall()
    out=[]
    for k,h,a,fh,fa in rows:
        gf,ga=(fh,fa) if h==team else (fa,fh)
        opp = a if h==team else h
        out.append({"date":k[:10],"kickoff":k,"gf":gf,"ga":ga,"pts":points(gf,ga),"opp":opp})
    return out

def strength(conn, team, before):
    hist=team_history(conn,team,before)[:10]
    if not hist: return 0.0
    return sum(x["pts"] for x in hist)/len(hist)

def next_fixture(conn, team, after):
    return conn.execute("""
        SELECT m.kickoff,m.home_team,m.away_team
        FROM matches m
        WHERE m.kickoff > ? AND (m.home_team=? OR m.away_team=?)
        ORDER BY m.kickoff LIMIT 1
    """,(after,team,team)).fetchone()

def importance_label(opp_strength, rest_days):
    if opp_strength >= 2.0: return "high"
    if opp_strength >= 1.4 or rest_days <= 4: return "medium"
    return "normal"

def main():
    con=sqlite3.connect(DB)
    matches=con.execute("""
        SELECT match_id,kickoff,home_team,away_team
        FROM matches WHERE status='finished' ORDER BY kickoff,match_id
    """).fetchall()
    for mid,kickoff,home,away in matches:
        for side,team in (("home",home),("away",away)):
            hist=team_history(con,team,kickoff)
            def pack(n):
                x=hist[:n]
                return json.dumps(x,separators=(",",":"))
            gf=sum(x["gf"] for x in hist[:10])/max(1,min(10,len(hist)))
            ga=sum(x["ga"] for x in hist[:10])/max(1,min(10,len(hist)))
            opps=[x["opp"] for x in hist[:10]]
            opp_strength=sum(strength(con,o,kickoff) for o in opps)/len(opps) if opps else 0.0
            con.execute("""
                INSERT OR REPLACE INTO team_features
                (match_id,team_side,last3,last5,last10,goals_for,goals_against,xg_for,xg_against,opponent_strength)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """,(mid,side,pack(3),pack(5),pack(10),gf,ga,None,None,op_strength))
            nxt=next_fixture(con,team,kickoff)
            if nxt:
                nk,nh,na=nxt
                opp=na if nh==team else nh
                try:
                    rest=(datetime.fromisoformat(nk[:19])-datetime.fromisoformat(kickoff[:19])).total_seconds()/86400
                except Exception:
                    rest=0
                os=strength(con,opp,kickoff)
                risk=0.75 if rest<=3 else 0.45 if rest<=4 else 0.2
                if os>=2.0: risk=min(1.0,risk+0.15)
                adj=-0.10 if risk>=0.7 else -0.05 if risk>=0.4 else 0.0
                con.execute("""
                    INSERT OR REPLACE INTO schedule_intent
                    (match_id,team_side,next_opponent,next_match_time,rest_days,next_match_importance,rotation_risk,motivation_adjustment)
                    VALUES (?,?,?,?,?,?,?,?)
                """,(mid,side,opp,nk,round(rest,2),importance_label(os,rest),risk,adj))
    con.commit()
    print("feature rows:",con.execute("SELECT count(*) FROM team_features").fetchone()[0])
    print("schedule rows:",con.execute("SELECT count(*) FROM schedule_intent").fetchone()[0])
    con.close()

if __name__=="__main__":
    main()
