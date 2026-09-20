import json, math, sqlite3
from datetime import datetime, timedelta

DB="football_model_database.sqlite"

def cutoff(kickoff):
    return (datetime.fromisoformat(kickoff[:19])-timedelta(hours=12)).isoformat(timespec="seconds")

def build(con,mid,side,cut):
    rows=con.execute("""
      SELECT p.player_id,p.player_name,p.position,p.starter,p.minutes_played,
             p.xg,p.xa,p.tackles,p.interceptions,p.clearances
      FROM player_match_stats p JOIN matches m ON m.match_id=p.match_id
      WHERE p.team_side=? AND m.kickoff < ? AND m.kickoff >= datetime(?, '-120 days')
      ORDER BY m.kickoff DESC
    """,(side,cut,cut)).fetchall()
    by={}
    for r in rows:
        pid,name,pos,starter,mins,xg,xa,tac,inter,clr=r
        if pid not in by: by[pid]=[]
        by[pid].append(r)
    feats=[]
    for pid,rs in by.items():
        w=[math.exp(-0.18*i) for i in range(len(rs))]
        sw=sum(w)
        starts=sum(w[i]*rs[i][3] for i in range(len(rs)))/sw
        app=sum(w[i]*(1 if rs[i][4]>0 else 0) for i in range(len(rs)))/sw
        em=sum(w[i]*rs[i][4] for i in range(len(rs)))/sw
        mins=max(90.0,em)
        xg90=sum(w[i]*rs[i][5] for i in range(len(rs)))/sw*90/mins
        xa90=sum(w[i]*rs[i][6] for i in range(len(rs)))/sw*90/mins
        def90=sum(w[i]*(rs[i][7]+rs[i][8]+rs[i][9]) for i in range(len(rs)))/sw*90/mins
        attack=0.65*xg90+0.35*xa90
        defense=0.02*def90
        influence=attack+0.01*def90
        feats.append((pid,rs[0][1],starts,app,em,attack,defense,influence,sum(x[4] for x in rs)))
    if not feats: return 0.0,0.0,0.0,0
    # Expected XI: top 11 by start probability, with expected minutes as tiebreaker.
    feats.sort(key=lambda x:(x[2],x[4]),reverse=True)
    xi=feats[:11]
    # Compare expected XI to a replacement baseline from the remaining squad.
    pool=feats[11:]
    rep_attack=sum(x[5] for x in pool)/len(pool) if pool else 0.0
    rep_def=sum(x[6] for x in pool)/len(pool) if pool else 0.0
    total_attack=sum((x[5]-rep_attack)*(x[4]/90.0)*x[2] for x in xi)
    total_def=sum((x[6]-rep_def)*(x[4]/90.0)*x[2] for x in xi)
    # Conservative log-rate deltas. These are player-data effects only.
    ad=max(-0.20,min(0.20,0.035*total_attack))
    dd=max(-0.20,min(0.20,0.035*total_def))
    uncertainty=max(0.0,min(1.0,1.0-min(1.0,sum(x[2] for x in xi)/11.0)))
    return ad,dd,uncertainty,len(xi)

def main():
    con=sqlite3.connect(DB)
    con.execute("DELETE FROM lineup_projection")
    rows=con.execute("SELECT match_id,kickoff FROM matches WHERE status='finished' ORDER BY kickoff").fetchall()
    populated=0
    for mid,ko in rows:
        cut=cutoff(ko)
        for side in ("home","away"):
            ad,dd,u,n=build(con,mid,side,cut)
            con.execute("""INSERT INTO lineup_projection
              (match_id,team_side,expected_start_strength,attack_delta,defense_delta,uncertainty,player_count,data_cutoff,source_status)
              VALUES (?,?,?,?,?,?,?,?,?)""",
              (mid,side,max(0.0,min(1.0,1.0-u)),ad,dd,u,n,cut,"SofaScore-derived" if n else "neutral-no-player-data"))
            populated += n>0
    con.commit()
    print(json.dumps({"lineup_rows":len(rows)*2,"populated_sides":populated,"source":"SofaScore public lineups","cutoff":"T-12h"},ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
