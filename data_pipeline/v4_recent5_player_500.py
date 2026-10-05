import json, math, sqlite3, os, csv
from datetime import datetime, timedelta

DB="football_model_database.sqlite"
OUT=os.getenv("OUT","model_validation/v4_recent5_player_500.csv")
SUMMARY=os.getenv("SUMMARY","model_validation/v4_recent5_player_500_summary.json")
MAP_PATH="model_validation/500_match_to_db_match_id_mapping.csv"
CUTOFF="2026-08-22T00:00:00"
N=8
RHO=-0.05
HIST_WINDOW=15

def hist(con,team,cutoff,limit=15):
    rows=con.execute("""SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
      AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
      ORDER BY m.kickoff DESC LIMIT ?""",(cutoff,team,team,limit)).fetchall()
    return [((fh,fa),a) if h==team else ((fa,fh),h) for _,h,a,fh,fa in rows]

def rates(h):
    if not h:return 1.15,1.15
    w=[math.exp(-0.12*i) for i in range(len(h))]; sw=sum(w)
    return max(.2,sum(x[0][0]*w[i] for i,x in enumerate(h))/sw),max(.2,sum(x[0][1]*w[i] for i,x in enumerate(h))/sw)

def dynamic_rates(con,team,cutoff):
    h=hist(con,team,cutoff,HIST_WINDOW)
    if not h:return 1.15,1.15
    w=[math.exp(-.045*i) for i in range(len(h))]; sw=sum(w); a=d=0
    for i,((gf,ga),opp) in enumerate(h):
        ogf,oga=rates(hist(con,opp,cutoff,HIST_WINDOW))
        a+=(gf/max(.75,oga/1.35))*w[i]; d+=(ga/max(.75,ogf/1.35))*w[i]
    return max(.2,a/sw),max(.2,d/sw)

def strength(con,team,before):
    x=hist(con,team,before,10)
    if not x:return 0
    return sum(3 if gf>ga else 1 if gf==ga else 0 for (gf,ga),_ in x)/len(x)

def tau(i,j,lh,la):
    if i==0 and j==0:return 1-lh*la*RHO
    if i==0 and j==1:return 1+lh*RHO
    if i==1 and j==0:return 1+la*RHO
    if i==1 and j==1:return 1-RHO
    return 1

def matrix(lh,la):
    m=[[math.exp(-lh)*lh**i/math.factorial(i)*math.exp(-la)*la**j/math.factorial(j)*tau(i,j,lh,la) for j in range(N)] for i in range(N)]
    s=sum(map(sum,m)); return [[v/s for v in row] for row in m]

def probs(lh,la):
    m=matrix(lh,la)
    return {"H":sum(m[i][j] for i in range(N) for j in range(N) if i>j),
            "D":sum(m[i][j] for i in range(N) for j in range(N) if i==j),
            "A":sum(m[i][j] for i in range(N) for j in range(N) if i<j)}

def lambdas(con,match,away_bias):
    mid,ko,h,a,fh,fa=match
    cutoff=(datetime.fromisoformat(ko[:19])-timedelta(hours=12)).isoformat(timespec="seconds")
    hgf,hga=dynamic_rates(con,h,cutoff); agf,aga=dynamic_rates(con,a,cutoff)
    for side,team in (("home",h),("away",a)):
        s=max(.75,min(2.25,strength(con,team,cutoff))); f=max(.94,min(1.06,(s/1.5)**.18))
        if side=="home":hgf*=f; hga/=f
        else:agf*=f; aga/=f
    lh=max(.15,min(4,.65+.58*hgf+.30*aga))
    la=max(.12,min(3.5,.58+.58*agf+.30*hga))-away_bias
    return max(.12,lh),max(.12,la),cutoff

def recent5(con,team,cutoff):
    x=hist(con,team,cutoff,5)
    if not x:return {"ppg":1,"gd":0,"gf":1.2,"ga":1.2}
    return {"ppg":sum(3 if gf>ga else 1 if gf==ga else 0 for (gf,ga),_ in x)/len(x),
            "gd":sum(gf-ga for (gf,ga),_ in x)/len(x),
            "gf":sum(gf for (gf,ga),_ in x)/len(x),
            "ga":sum(ga for (gf,ga),_ in x)/len(x)}

def player_form(con,team,cutoff):
    # Only the target team's rows from its five most recent pre-cutoff matches.
    mids=con.execute("""SELECT m.match_id,m.home_team,m.away_team
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff<? AND (m.home_team=? OR m.away_team=?)
      AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
      ORDER BY m.kickoff DESC LIMIT 5""",(cutoff,team,team)).fetchall()
    if not mids:return {"attack":0,"defense":0,"influence":0,"coverage":0}
    rows=[]
    for mid,home,away in mids:
        side="home" if home==team else "away"
        rows.extend(con.execute("""SELECT p.minutes_played,p.rating,p.xg,p.xa,p.shots,p.key_passes,p.tackles,p.interceptions
          FROM player_match_stats p
          WHERE p.match_id=? AND p.team_side=? AND p.minutes_played>0""",(mid,side)).fetchall())
    if not rows:return {"attack":0,"defense":0,"influence":0,"coverage":0}
    mins=sum(float(r[0] or 0) for r in rows)
    if mins<=0:return {"attack":0,"defense":0,"influence":0,"coverage":0}
    w=lambda r:max(1.0,float(r[0] or 0))
    attack=sum(w(r)*(float(r[2] or 0)+.7*float(r[3] or 0)+.03*float(r[4] or 0)+.05*float(r[5] or 0)) for r in rows)/mins*90
    defense=sum(w(r)*(.02*float(r[6] or 0)+.03*float(r[7] or 0)) for r in rows)/mins*90
    rating=sum(w(r)*float(r[1] or 0) for r in rows)/mins
    return {"attack":attack,"defense":defense,"influence":max(0,rating-6.5),"coverage":len(rows)}

def feature_pair(con,match):
    mid,ko,h,a,fh,fa=match
    cutoff=(datetime.fromisoformat(ko[:19])-timedelta(hours=12)).isoformat(timespec="seconds")
    rh,ra=recent5(con,h,cutoff),recent5(con,a,cutoff)
    ph,pa=player_form(con,h,cutoff),player_form(con,a,cutoff)
    form_h=(rh["ppg"]-1.35)/1.65 + .22*rh["gd"]
    form_a=(ra["ppg"]-1.35)/1.65 + .22*ra["gd"]
    player_h=(ph["attack"]-0.18)+.10*ph["influence"]
    player_a=(pa["attack"]-0.18)+.10*pa["influence"]
    return form_h,form_a,player_h,player_a,ph["coverage"],pa["coverage"]

def eval_arm(con,matches,away_bias,bf,bp):
    d={"n":0,"correct":0,"logloss":0}; rows=[]
    for match in matches:
        lh,la,cutoff=lambdas(con,match,away_bias)
        fhf,faf,phf,paf,_,_=feature_pair(con,match)
        lh*=math.exp(max(-.12,min(.12,bf*fhf+bp*phf)))
        la*=math.exp(max(-.12,min(.12,bf*faf+bp*paf)))
        p=probs(lh,la); fh,fa=match[4],match[5]
        actual="H" if fh>fa else "D" if fh==fa else "A"
        pred=max(p,key=p.get)
        d["n"]+=1; d["correct"]+=pred==actual; d["logloss"]-=math.log(max(1e-12,p[actual]))
        rows.append((match,p,pred,actual,lh,la))
    return d,rows

def main():
    con=sqlite3.connect(DB)
    allm=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL ORDER BY m.kickoff,m.match_id""").fetchall()
    train=[m for m in allm if m[1]<CUTOFF]
    # Resolve the frozen 500 by the checked-in mapping file, independent of cwd.
    map_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),MAP_PATH)
    try:
        with open(map_path,encoding="utf-8") as f:
            frozen_ids={r["db_match_id"].strip() for r in csv.DictReader(f) if r.get("db_match_id")}
    except Exception as e:
        raise SystemExit(f"FROZEN_500_READ_ERROR:{type(e).__name__}:{e}")
    frozen=[m for m in allm if m[0] in frozen_ids]
    # The checked-in mapping can outlive a refreshed SQLite DB. Fall back to
    # unique date + normalized team names + final score matching.
    if len(frozen)!=500:
        def norm(s):
            return "".join(ch.lower() for ch in (s or "") if ch.isalnum())
        frozen=[]
        used=set()
        with open(map_path,encoding="utf-8") as f:
            for r in csv.DictReader(f):
                mid=r.get("db_match_id","").strip()
                hit=next((m for m in allm if m[0]==mid),None)
                if hit is None:
                    date=r.get("date","").strip()
                    hh,aa=r.get("db_home") or r.get("home",""),r.get("db_away") or r.get("away","")
                    hs,as_=int(r.get("db_home_score") or r.get("home_score")),int(r.get("db_away_score") or r.get("away_score"))
                    dbko=(r.get("db_kickoff") or "").strip()
                    def team_match(x,y):
                        nx,ny=norm(x),norm(y)
                        return nx==ny or nx in ny or ny in nx
                    cand=[m for m in allm
                          if m[1][:10]==date and m[4]==hs and m[5]==as_
                          and team_match(m[2],hh) and team_match(m[3],aa)]
                    if dbko:
                        exact=[m for m in cand if m[1].replace(" ","")[:16]==dbko.replace(" ","")[:16]]
                        if len(exact)==1: cand=exact
                    if len(cand)==1: hit=cand[0]
                if hit is None or hit[0] in used:
                    raise SystemExit("FROZEN_500_MAPPING_AMBIGUOUS_OR_MISSING")
                used.add(hit[0]); frozen.append(hit)
    if len(frozen)!=500 or len({m[0] for m in frozen})!=500:
        raise SystemExit(f"FROZEN_500_INVALID:rows={len(frozen)},unique_db={len({m[0] for m in frozen})}")
    diffs=[]
    for m in train:
        lh,la,cut=lambdas(con,m,0.0)
        diffs.append(la-m[5])
    away_bias=sum(diffs)/len(diffs)
    split=int(len(train)*.70); cal=train[split:]
    grid_b=[0,.01,.02,.03,.04,.05,.06,.08,.10,.12]
    best=(0,0,float("inf"))
    for bf in grid_b:
        for bp in grid_b:
            d,_=eval_arm(con,cal,away_bias,bf,bp)
            ll=d["logloss"]/d["n"]
            if ll<best[2]:best=(bf,bp,ll)
    bf,bp,ll=best
    base_d,base_rows=eval_arm(con,frozen,away_bias,0,0)
    comb_d,comb_rows=eval_arm(con,frozen,away_bias,bf,bp)
    changed=sum(a[2]!=b[2] for a,b in zip(base_rows,comb_rows))
    base_counts={k:sum(r[2]==k for r in base_rows) for k in "HDA"}
    comb_counts={k:sum(r[2]==k for r in comb_rows) for k in "HDA"}
    coverage_h=sum(r[4]!=0 or r[5]!=0 for r in comb_rows)
    with open(OUT,"w",encoding="utf-8") as f:
        f.write("match_id,kickoff,home,away,base_pred,combined_pred,actual,base_lh,base_la,combined_lh,combined_la\n")
        for b,c in zip(base_rows,comb_rows):
            m=b[0];f.write(f"{m[0]},{m[1]},{m[2]},{m[3]},{b[2]},{c[2]},{b[3]},{b[4]:.6f},{b[5]:.6f},{c[4]:.6f},{c[5]:.6f}\n")
    summary={"experiment":"V4 recent-5 team form + player data combined module on frozen 500",
      "production_status":"EXPERIMENT_ONLY","baseline":"51.2% accepted pipeline: HIST_WINDOW=15 + away lambda bias correction + unchanged Dixon-Coles",
      "n":500,"cutoff":CUTOFF,"train_matches":len(train),"calibration_matches":len(cal),
      "away_lambda_bias":away_bias,"selected_form_coef":bf,"selected_player_coef":bp,"calibration_logloss":ll,
      "baseline_correct":base_d["correct"],"baseline_accuracy":base_d["correct"]/500,"baseline_pred_counts":base_counts,
      "combined_correct":comb_d["correct"],"combined_accuracy":comb_d["correct"]/500,"combined_pred_counts":comb_counts,
      "actual_counts":{k:sum(r[3]==k for r in comb_rows) for k in "HDA"},
      "changed_predictions":changed,"improvement_correct":comb_d["correct"]-base_d["correct"],
      "player_coverage_matches":coverage_h}
    with open(SUMMARY,"w",encoding="utf-8") as f:json.dump(summary,f,ensure_ascii=False,indent=2)
    print(json.dumps(summary,ensure_ascii=False))
if __name__=="__main__":main()
