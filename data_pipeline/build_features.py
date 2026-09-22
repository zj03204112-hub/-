import bisect, json, sqlite3
from datetime import datetime

DB="football_model_database.sqlite"

def points(gf,ga):
    return 3 if gf>ga else 1 if gf==ga else 0

def main():
    con=sqlite3.connect(DB)

    # Load once into memory. The previous implementation repeatedly queried SQLite
    # inside nested opponent-adjustment loops (millions of small queries on the
    # 8k+ match dataset), which could make the workflow appear hung.
    rows=con.execute("""
      SELECT m.match_id,m.kickoff,m.home_team,m.away_team,m.status,
             r.ft_home,r.ft_away
      FROM matches m LEFT JOIN results r ON r.match_id=m.match_id
      ORDER BY m.kickoff,m.match_id
    """).fetchall()

    finished_by_team={}
    fixtures_by_team={}

    for mid,kickoff,home,away,status,fh,fa in rows:
        for team in (home,away):
            fixtures_by_team.setdefault(team,[]).append((kickoff,mid,home,away))
        if status=="finished" and fh is not None and fa is not None:
            for team,opp,gf,ga in (
                (home,away,fh,fa),
                (away,home,fa,fh),
            ):
                finished_by_team.setdefault(team,[]).append(
                    (kickoff,mid,opp,gf,ga)
                )

    for team in finished_by_team:
        finished_by_team[team].sort(key=lambda x:(x[0],x[1]))
    for team in fixtures_by_team:
        fixtures_by_team[team].sort(key=lambda x:(x[0],x[1]))

    def history(team,before):
        games=finished_by_team.get(team,[])
        keys=[(x[0],x[1]) for x in games]
        idx=bisect.bisect_left(keys,(before,""))
        selected=games[max(0,idx-12):idx]
        selected=selected[::-1]
        return [
            {"kickoff":k,"date":k[:10],"gf":gf,"ga":ga,
             "pts":points(gf,ga),"opp":opp}
            for k,mid,opp,gf,ga in selected
        ]

    def strength(team,before):
        x=history(team,before)[:10]
        return sum(v["pts"] for v in x)/len(x) if x else 0.0

    strength_cache={}
    def cached_strength(team,before):
        key=(team,before)
        if key not in strength_cache:
            strength_cache[key]=strength(team,before)
        return strength_cache[key]

    opp_adj_cache={}
    def opponent_adjusted_strength(team,before):
        key=(team,before)
        if key in opp_adj_cache:
            return opp_adj_cache[key]
        x=history(team,before)[:10]
        if not x:
            opp_adj_cache[key]=0.0
            return 0.0
        weights=[1.0/(1.0+i*0.12) for i in range(len(x))]
        vals=[]
        for i,v in enumerate(x):
            os=cached_strength(v["opp"],before)
            vals.append((
                v["pts"]*(0.75+0.25*max(0.5,min(1.5,os/1.5))),
                weights[i]
            ))
        out=sum(v*w for v,w in vals)/sum(weights)
        opp_adj_cache[key]=out
        return out

    def next_fixture(team,after):
        games=fixtures_by_team.get(team,[])
        keys=[(x[0],x[1]) for x in games]
        idx=bisect.bisect_right(keys,(after,10**30))
        return games[idx] if idx<len(games) else None

    matches=con.execute("""
      SELECT match_id,kickoff,home_team,away_team
      FROM matches WHERE status='finished'
      ORDER BY kickoff,match_id
    """).fetchall()

    con.execute("DELETE FROM team_features")
    con.execute("DELETE FROM schedule_intent")

    for mid,kickoff,home,away in matches:
        for side,team in (("home",home),("away",away)):
            hist=history(team,kickoff)
            pack=lambda n: json.dumps(hist[:n],ensure_ascii=False,separators=(",",":"))
            n=max(1,min(10,len(hist)))
            gf=sum(x["gf"] for x in hist[:10])/n
            ga=sum(x["ga"] for x in hist[:10])/n
            opps=[x["opp"] for x in hist[:10]]
            os=(
                sum(opponent_adjusted_strength(o,kickoff) for o in opps)/len(opps)
                if opps else 0.0
            )

            con.execute("""
              INSERT INTO team_features
              (match_id,team_side,last3,last5,last10,goals_for,goals_against,
               xg_for,xg_against,opponent_strength)
              VALUES (?,?,?,?,?,?,?,?,?,?)
            """,(mid,side,pack(3),pack(5),pack(10),gf,ga,None,None,os))

            nxt=next_fixture(team,kickoff)
            if not nxt:
                continue
            nk,nmid,nh,na=nxt
            opp=na if nh==team else nh

            try:
                rest=max(
                    0,
                    (datetime.fromisoformat(str(nk)[:19]) -
                     datetime.fromisoformat(str(kickoff)[:19])).total_seconds()/86400
                )
            except Exception:
                rest=0.0

            os2=cached_strength(opp,kickoff)
            risk=0.75 if rest<=3 else 0.45 if rest<=4 else 0.2
            if os2>=2.0:
                risk=min(1.0,risk+0.15)
            importance="high" if os2>=2.0 else ("medium" if rest<=4 else "normal")
            adj=-0.10 if risk>=0.7 else (-0.05 if risk>=0.4 else 0.0)

            con.execute("""
              INSERT INTO schedule_intent
              (match_id,team_side,next_opponent,next_match_time,rest_days,
               next_match_importance,rotation_risk,motivation_adjustment)
              VALUES (?,?,?,?,?,?,?,?)
            """,(mid,side,opp,nk,round(rest,2),importance,risk,adj))

    con.commit()
    print("feature rows:",con.execute("SELECT COUNT(*) FROM team_features").fetchone()[0])
    print("schedule rows:",con.execute("SELECT COUNT(*) FROM schedule_intent").fetchone()[0])
    print("cached strength keys:",len(strength_cache))
    print("cached opponent-adjusted keys:",len(opp_adj_cache))
    con.close()

if __name__=="__main__":
    main()
