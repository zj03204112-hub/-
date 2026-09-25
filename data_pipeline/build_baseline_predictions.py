import json, math, sqlite3
from datetime import datetime, timedelta

DB="football_model_database.sqlite"
MODEL="dc_strength_t12_v3"
HIST_LIMIT=int(__import__("os").getenv("HIST_LIMIT","30"))

def hist(con,team,cutoff):
    rows=con.execute("""
      SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
      ORDER BY m.kickoff DESC LIMIT ?
    """,(cutoff,team,team,HIST_LIMIT)).fetchall()
    out=[]
    for k,h,a,fh,fa in rows:
        gf,ga=(fh,fa) if h==team else (fa,fh)
        out.append((gf,ga))
    return out

def rates(h):
    if not h: return 1.15,1.15
    w=[math.exp(-0.12*i) for i in range(len(h))]
    sw=sum(w)
    gf=sum(x[0]*w[i] for i,x in enumerate(h))/sw
    ga=sum(x[1]*w[i] for i,x in enumerate(h))/sw
    return max(0.2,gf),max(0.2,ga)

def pois(lam,k):
    return math.exp(-lam)*lam**k/math.factorial(k)

RHO=-0.05
MAX_GOALS=8

def tau(x,y,lh,la,rho=RHO):
    if x==0 and y==0: return 1-lh*la*rho
    if x==0 and y==1: return 1+lh*rho
    if x==1 and y==0: return 1+la*rho
    if x==1 and y==1: return 1-rho
    return 1.0

def dc_matrix(lh,la):
    m=[]
    for i in range(MAX_GOALS):
        row=[]
        for j in range(MAX_GOALS):
            row.append(pois(lh,i)*pois(la,j)*tau(i,j,lh,la))
        m.append(row)
    total=sum(sum(r) for r in m)
    return [[v/total for v in row] for row in m]

MARKET_PRIORITY={"hhad":5,"asian_handicap_avg":4,"sofascore_asian_featured":3,"sgodds_open":2,"football_data_ah_close":2,"football_data_ah_bookmaker":1,"football_data_ah_open":1}

def canonical_line(x):
    try:
        v=float(x)
        return int(round(v)) if abs(v-round(v))<1e-9 else None
    except Exception:
        return None

def handicap_probs(matrix,line):
    p={"H":0.0,"D":0.0,"A":0.0}
    for i,row in enumerate(matrix):
        for j,v in enumerate(row):
            d=i+line-j
            p["H" if d>0 else "D" if d==0 else "A"]+=v
    return p

def conditioned_scores(matrix,line,pred,n=2):
    xs=[]
    for i,row in enumerate(matrix):
        for j,v in enumerate(row):
            d=i+line-j
            r="H" if d>0 else "D" if d==0 else "A"
            if r==pred: xs.append((v,i,j))
    total=sum(x[0] for x in xs)
    if not total:
        return sorted(((v,i,j) for i,row in enumerate(matrix) for j,v in enumerate(row)),reverse=True)[:n]
    return sorted(((v/total,i,j) for v,i,j in xs),reverse=True)[:n]

def market_line_at_cutoff(con,match_id,cutoff):
    rows=con.execute("""
      SELECT handicap,pool_code,captured_at FROM sporttery_market
      WHERE match_id=? AND handicap IS NOT NULL AND captured_at<=?
      AND pool_code IN ('hhad','asian_handicap_avg','sofascore_asian_featured','sgodds_open','football_data_ah_close','football_data_ah_bookmaker','football_data_ah_open')
      ORDER BY captured_at DESC
    """,(match_id,cutoff)).fetchall()
    best=None
    for handicap,pool,captured in rows:
        line=canonical_line(handicap)
        if line is None: continue
        key=(captured,MARKET_PRIORITY.get(pool,0))
        if best is None or key>(best[0],best[1]): best=(captured,MARKET_PRIORITY.get(pool,0),line,pool)
    return (best[2],best[3]) if best else (None,None)

def opponent_adjusted_rates(con,team,side,match_id,hgf,hga):
    row=con.execute(
        "SELECT opponent_strength FROM team_features WHERE match_id=? AND team_side=?",
        (match_id,side)
    ).fetchone()
    os=float(row[0]) if row and row[0] is not None else 1.5
    os=max(0.75,min(2.25,os))
    # Modest opponent-quality correction; avoid overfitting early-season samples.
    factor=max(0.90,min(1.10,(os/1.5)**0.25))
    return hgf*factor, hga/factor, os

def main():
    con=sqlite3.connect(DB)
    matches=con.execute("""
      SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL
      ORDER BY m.kickoff,m.match_id
    """).fetchall()
    probs=[]; correct=0; brier=0; logloss=0; n=0
    con.execute("DELETE FROM prediction_snapshot WHERE notes LIKE ?",(f'%"model":"{MODEL}"%',))
    for mid,kickoff,home,away,fh,fa in matches:
        try:
            ko=datetime.fromisoformat(kickoff[:19])
        except Exception:
            ko=datetime.fromisoformat(kickoff[:10]+"T12:00:00")
        cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
        hh=hist(con,home,cutoff); ah=hist(con,away,cutoff)
        hgf,hga=rates(hh); agf,aga=rates(ah)
        hgf,hga,hos=opponent_adjusted_rates(con,home,"home",mid,hgf,hga)
        agf,aga,aos=opponent_adjusted_rates(con,away,"away",mid,agf,aga)
        lam_h=max(0.15,min(4.0,0.65+0.58*hgf+0.30*aga))
        lam_a=max(0.12,min(3.5,0.58+0.58*agf+0.30*hga))
        # Next-match congestion/rotation adjustment, using only schedule metadata.
        sched=con.execute(
            "SELECT rotation_risk,motivation_adjustment FROM schedule_intent WHERE match_id=? AND team_side=?",
            (mid,"home")
        ).fetchone()
        if sched:
            lam_h*=max(0.94,1.0+float(sched[1] or 0.0))
        sched=con.execute(
            "SELECT rotation_risk,motivation_adjustment FROM schedule_intent WHERE match_id=? AND team_side=?",
            (mid,"away")
        ).fetchone()
        if sched:
            lam_a*=max(0.94,1.0+float(sched[1] or 0.0))
        # Optional T-12h lineup/injury layer. Zero impact when no sourced player data exists.
        for side in ("home","away"):
            lp=con.execute("SELECT attack_delta,defense_delta FROM lineup_projection WHERE match_id=? AND team_side=?",(mid,side)).fetchone()
            if lp:
                ad=max(-0.25,min(0.25,float(lp[0] or 0.0)))
                dd=max(-0.25,min(0.25,float(lp[1] or 0.0)))
                if side=="home":
                    lam_h*=math.exp(ad); lam_a*=math.exp(dd)
                else:
                    lam_a*=math.exp(ad); lam_h*=math.exp(dd)
        matrix=dc_matrix(lam_h,lam_a)
        ph=sum(matrix[i][j] for i in range(MAX_GOALS) for j in range(MAX_GOALS) if i>j)
        pd=sum(matrix[i][j] for i in range(MAX_GOALS) for j in range(MAX_GOALS) if i==j)
        pa=sum(matrix[i][j] for i in range(MAX_GOALS) for j in range(MAX_GOALS) if i<j)
        pred=max(((ph,"H"),(pd,"D"),(pa,"A")))[1]
        line,pool=market_line_at_cutoff(con,mid,cutoff)
        hp=None; hpred=None
        if line is not None:
            hp=handicap_probs(matrix,line)
            hpred=max(hp,key=hp.get)
            ps=conditioned_scores(matrix,line,hpred,2)
        else:
            ps=sorted(((matrix[i][j],i,j) for i in range(7) for j in range(7)),reverse=True)[:2]
        s1=f"{ps[0][1]}-{ps[0][2]}"; s2=f"{ps[1][1]}-{ps[1][2]}"
        actual="H" if fh>fa else "D" if fh==fa else "A"
        p={"H":ph,"D":pd,"A":pa}; y={"H":0,"D":0,"A":0}; y[actual]=1
        brier+=(p["H"]-y["H"])**2+(p["D"]-y["D"])**2+(p["A"]-y["A"])**2
        logloss-=math.log(max(1e-12,p[actual]))
        correct+=pred==actual; n+=1
        probs.append((max(p.values()),pred==actual))
        conf=round(max(p.values()),4)
        con.execute("""
          INSERT INTO prediction_snapshot
          (match_id,snapshot_time,data_cutoff,handicap,handicap_prediction,score_1,score_2,half_full,model_confidence,notes)
          VALUES (?,?,?,?,?,?,?,?,?,?)
        """,(mid,cutoff,cutoff,line,hpred,s1,s2,None,conf,
             json.dumps({"model":MODEL,"p":p,"handicap_p":hp,"market_pool":pool,
                         "lambda":[lam_h,lam_a],"actual":actual,"rho":RHO},separators=(",",":"))))
    bins=[]
    for lo in [0.5,0.6,0.7,0.8,0.9]:
        xs=[hit for c,hit in probs if lo<=c<min(lo+0.1,1.0001)]
        bins.append({"bin":f"{lo:.1f}-{lo+0.1:.1f}","n":len(xs),"hit_rate":round(sum(xs)/len(xs),4) if xs else None})
    metrics={"model":MODEL,"n":n,"accuracy":round(correct/n,4) if n else None,
             "brier":round(brier/n,4) if n else None,"logloss":round(logloss/n,4) if n else None,
             "confidence_bins":bins,"data_cutoff_rule":"kickoff minus 12 hours; prior completed results only"}
    with open("data/baseline_backtest.json","w",encoding="utf-8") as f: json.dump(metrics,f,ensure_ascii=False,indent=2)
    con.commit(); con.close(); print(json.dumps(metrics,ensure_ascii=False))

if __name__=="__main__": main()
