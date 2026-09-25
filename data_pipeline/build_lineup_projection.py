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
        pos=(rs[0][2] or "O")
        feats.append((pid,rs[0][1],pos,starts,app,em,attack,defense,influence,sum(x[4] for x in rs)))
    if not feats: return 0.0,0.0,0.0,0
    # Position-constrained expected XI. We do not use the final match XI;
    # only historical T-12h player records before the cutoff are eligible.
    feats.sort(key=lambda x:(x[3],x[5]),reverse=True)
    buckets={"G":[],"D":[],"M":[],"F":[],"O":[]}
    for pid,name,pos,starts,app,em,attack,defense,influence,totalmins in feats:
        buckets.setdefault(pos,[]).append((pid,name,starts,app,em,attack,defense,influence,totalmins,pos))
    for k in buckets:
        buckets[k].sort(key=lambda x:(x[2],x[4]),reverse=True)

    xi=[]
    if buckets["G"]:
        xi.append(buckets["G"][0])
    # Football-plausible positional bounds; remaining slots are filled by
    # strongest available players without forcing an artificial formation.
    targets={"D":3,"M":2,"F":1}
    maxes={"D":5,"M":5,"F":4}
    used={x[0] for x in xi}
    for pos,nmin in targets.items():
        for x in buckets[pos]:
            if len([z for z in xi if z[-1]==pos])>=nmin or len(xi)>=11:
                break
            xi.append(x); used.add(x[0])
    remaining=[]
    for x in buckets["D"]+buckets["M"]+buckets["F"]+buckets["O"]:
        if x[0] not in used:
            remaining.append(x)
    remaining.sort(key=lambda x:(x[2],x[4]),reverse=True)
    for x in remaining:
        pos=x[-1]
        count=sum(1 for z in xi if z[-1]==pos)
        if pos in maxes and count>=maxes[pos]:
            continue
        if len(xi)>=11:
            break
        xi.append(x); used.add(x[0])
    # If unusual data leaves fewer than 11, fill from all remaining records.
    if len(xi)<11:
        for x in remaining:
            if x[0] not in used:
                xi.append(x); used.add(x[0])
                if len(xi)>=11: break

    # Compare expected XI to a replacement baseline from the remaining squad.
    pool=feats[11:]
    rep_attack=sum(x[5] for x in pool)/len(pool) if pool else 0.0
    rep_def=sum(x[6] for x in pool)/len(pool) if pool else 0.0
    total_attack=sum((x[6]-rep_attack)*(x[5]/90.0) for x in xi)
    total_def=sum((x[7]-rep_def)*(x[5]/90.0) for x in xi)
    # Conservative log-rate deltas. These are player-data effects only.
    ad=max(-0.20,min(0.20,0.035*total_attack))
    dd=max(-0.20,min(0.20,0.035*total_def))
    uncertainty=max(0.0,min(1.0,1.0-len(xi)/11.0))
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
              (mid,side,max(0.0,min(1.0,1.0-u)),ad,dd,u,n,cut,"FotMob-derived" if n else "neutral-no-player-data"))
            populated += n>0
    con.commit()
    print(json.dumps({"lineup_rows":len(rows)*2,"populated_sides":populated,"source":"SofaScore public lineups","cutoff":"T-12h"},ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
