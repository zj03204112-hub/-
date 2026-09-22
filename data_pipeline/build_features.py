import json, sqlite3
from datetime import datetime
from collections import defaultdict, deque

DB="football_model_database.sqlite"

def points(gf,ga):
    return 3 if gf>ga else 1 if gf==ga else 0

def pack(hist,n):
    return json.dumps(list(hist)[-n:], ensure_ascii=False, separators=(",",":"))

def strength_from_hist(hist):
    x=list(hist)[-10:]
    return sum(v["pts"] for v in x)/len(x) if x else 0.0

def opponent_adjusted_from_hist(histories,team):
    x=list(histories.get(team,()))[-10:]
    if not x:
        return 0.0
    weights=[1.0/(1.0+i*0.12) for i in range(len(x))]
    vals=[]
    for i,v in enumerate(x):
        os=strength_from_hist(histories.get(v["opp"],()))
        vals.append((v["pts"]*(0.75+0.25*max(0.5,min(1.5,os/1.5))),weights[i]))
    return sum(v*w for v,w in vals)/sum(weights)

def main():
    con=sqlite3.connect(DB)
    finished=con.execute("""SELECT match_id,kickoff,home_team,away_team
      FROM matches WHERE status='finished' ORDER BY kickoff,match_id""").fetchall()
    all_matches=con.execute("""SELECT kickoff,home_team,away_team
      FROM matches ORDER BY kickoff""").fetchall()

    # Precompute each team's next scheduled fixture once instead of issuing
    # a SQL query for every team-match pair.
    next_fixture={}
    seen=defaultdict(list)
    for k,h,a in all_matches:
        seen[h].append((k,h,a))
        seen[a].append((k,h,a))
    for team,rows in seen.items():
        rows.sort(key=lambda x:x[0])
        next_fixture[team]=rows

    con.execute("DELETE FROM team_features")
    con.execute("DELETE FROM schedule_intent")

    # Keep rolling pre-match histories in memory. The old implementation
    # executed nested SQL history/strength queries for every match and became
    # effectively O(matches * history * nested history), causing step 17 to
    # stall. This preserves the same look-back definitions without that cost.
    histories=defaultdict(lambda: deque(maxlen=12))

    def get_next(team,kickoff):
        for nk,nh,na in next_fixture.get(team,()):
            if nk > kickoff:
                return nk,nh,na
        return None

    feature_rows=[]
    schedule_rows=[]

    for mid,kickoff,home,away in finished:
        for side,team in (("home",home),("away",away)):
            hist=list(histories.get(team,()))
            n=max(1,min(10,len(hist)))
            gf=sum(x["gf"] for x in hist[-10:])/n
            ga=sum(x["ga"] for x in hist[-10:])/n
            opps=[x["opp"] for x in hist[-10:]]
            os=(sum(opponent_adjusted_from_hist(histories,o) for o in opps)/len(opps)
                if opps else 0.0)

            feature_rows.append((mid,side,pack(hist,3),pack(hist,5),pack(hist,10),
                                 gf,ga,None,None,os))

            nxt=get_next(team,kickoff)
            if nxt:
                nk,nh,na=nxt
                opp=na if nh==team else nh
                try:
                    rest=max(0,(datetime.fromisoformat(str(nk)[:19])-
                                 datetime.fromisoformat(str(kickoff)[:19])).total_seconds()/86400)
                except Exception:
                    rest=0.0
                os2=strength_from_hist(histories.get(opp,()))
                risk=0.75 if rest<=3 else 0.45 if rest<=4 else 0.2
                if os2>=2.0:
                    risk=min(1.0,risk+0.15)
                importance="high" if os2>=2.0 else ("medium" if rest<=4 else "normal")
                adj=-0.10 if risk>=0.7 else (-0.05 if risk>=0.4 else 0.0)
                schedule_rows.append((mid,side,opp,nk,round(rest,2),
                                      importance,risk,adj))

        # Append this completed match only after both sides' pre-match
        # features have been calculated, preserving leakage-safe behavior.
        fhfa=con.execute("SELECT ft_home,ft_away FROM results WHERE match_id=?",
                         (mid,)).fetchone()
        if fhfa and fhfa[0] is not None and fhfa[1] is not None:
            fh,fa=fhfa
            histories[home].append({"kickoff":kickoff,"date":str(kickoff)[:10],
                                    "gf":fh,"ga":fa,"pts":points(fh,fa),"opp":away})
            histories[away].append({"kickoff":kickoff,"date":str(kickoff)[:10],
                                    "gf":fa,"ga":fh,"pts":points(fa,fh),"opp":home})

    con.executemany("""INSERT INTO team_features
      (match_id,team_side,last3,last5,last10,goals_for,goals_against,xg_for,xg_against,opponent_strength)
      VALUES (?,?,?,?,?,?,?,?,?,?)""", feature_rows)
    con.executemany("""INSERT INTO schedule_intent
      (match_id,team_side,next_opponent,next_match_time,rest_days,next_match_importance,rotation_risk,motivation_adjustment)
      VALUES (?,?,?,?,?,?,?,?)""", schedule_rows)
    con.commit()
    print("feature rows:",len(feature_rows))
    print("schedule rows:",len(schedule_rows))
    con.close()

if __name__=="__main__":
    main()
