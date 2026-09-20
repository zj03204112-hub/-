import json, math, sqlite3
from datetime import datetime, timedelta

DB="football_model_database.sqlite"
OUT="data/model_comparison.json"
RHO=-0.05
N=8

def hist(con,team,cutoff):
    rows=con.execute("""SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.kickoff < ? AND (m.home_team=? OR m.away_team=?)
      AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
      ORDER BY m.kickoff DESC LIMIT 30""",(cutoff,team,team)).fetchall()
    return [((fh,fa) if h==team else (fa,fh), a if h==team else h)
            for _,h,a,fh,fa in rows]

def rates(h):
    if not h: return 1.15,1.15
    w=[math.exp(-0.12*i) for i in range(len(h))]
    sw=sum(w)
    return (max(.2,sum(x[0][0]*w[i] for i,x in enumerate(h))/sw),
            max(.2,sum(x[0][1]*w[i] for i,x in enumerate(h))/sw))

def dynamic_rates(con,team,cutoff):
    h=hist(con,team,cutoff)
    if not h: return 1.15,1.15
    w=[math.exp(-0.045*i) for i in range(len(h))]
    sw=sum(w); attack=defence=0.0
    for i,((gf,ga),opp) in enumerate(h):
        ogf,oga=rates(hist(con,opp,cutoff))
        opp_def=max(.75,oga/1.35)
        opp_att=max(.75,ogf/1.35)
        attack += (gf/opp_def)*w[i]
        defence += (ga/opp_att)*w[i]
    return max(.2,attack/sw), max(.2,defence/sw)

def strength(con,team,before):
    x=hist(con,team,before)[:10]
    if not x: return 0.0
    pts=[3 if gf>ga else 1 if gf==ga else 0 for (gf,ga),_ in x]
    return sum(pts)/len(pts)

def tau(i,j,lh,la):
    if i==0 and j==0: return 1-lh*la*RHO
    if i==0 and j==1: return 1+lh*RHO
    if i==1 and j==0: return 1+la*RHO
    if i==1 and j==1: return 1-RHO
    return 1.0

def matrix(lh,la,dc):
    m=[[math.exp(-lh)*lh**i/math.factorial(i)*math.exp(-la)*la**j/math.factorial(j)*
        (tau(i,j,lh,la) if dc else 1.0) for j in range(N)] for i in range(N)]
    s=sum(map(sum,m))
    return [[v/s for v in row] for row in m]

def metrics_add(d,p,actual):
    y={"H":0,"D":0,"A":0}; y[actual]=1
    d["n"]+=1; d["correct"]+=int(max(p,key=p.get)==actual)
    d["brier"]+=sum((p[k]-y[k])**2 for k in y)
    d["logloss"]-=math.log(max(1e-12,p[actual]))

def finish(d):
    return {"n":d["n"],"accuracy":round(d["correct"]/d["n"],4),
            "brier":round(d["brier"]/d["n"],4),"logloss":round(d["logloss"]/d["n"],4)}

def model_lambdas(con,model,match):
    mid,kickoff,home,away,fh,fa=match
    ko=datetime.fromisoformat(kickoff[:19])
    cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
    if model=="v4":
        hgf,hga=dynamic_rates(con,home,cutoff); agf,aga=dynamic_rates(con,away,cutoff)
        for side,os in (("home",strength(con,home,cutoff)),("away",strength(con,away,cutoff))):
            os=max(.75,min(2.25,os)); factor=max(.94,min(1.06,(os/1.5)**.18))
            if side=="home": hgf*=factor; hga/=factor
            else: agf*=factor; aga/=factor
    else:
        hh=hist(con,home,cutoff); ah=hist(con,away,cutoff)
        hgf,hga=rates(hh); agf,aga=rates(ah)
        if model=="v3":
            for side,os in (("home",strength(con,home,cutoff)),("away",strength(con,away,cutoff))):
                os=max(.75,min(2.25,os)); factor=max(.90,min(1.10,(os/1.5)**.25))
                if side=="home": hgf*=factor; hga/=factor
                else: agf*=factor; aga/=factor
            for side in ("home","away"):
                row=con.execute("SELECT motivation_adjustment FROM schedule_intent WHERE match_id=? AND team_side=?",(mid,side)).fetchone()
                if row:
                    adj=max(.94,1+float(row[0] or 0))
                    if side=="home": hgf=max(.2,hgf*adj)
                    else: agf=max(.2,agf*adj)
    lh=max(.15,min(4.0,.65+.58*hgf+.30*aga))
    la=max(.12,min(3.5,.58+.58*agf+.30*hga))
    return lh,la

def run_model(con,model,match):
    lh,la=model_lambdas(con,model,match)
    fh,fa=match[4],match[5]
    m=matrix(lh,la,model!="v1")
    p={"H":sum(m[i][j] for i in range(N) for j in range(N) if i>j),
       "D":sum(m[i][j] for i in range(N) for j in range(N) if i==j),
       "A":sum(m[i][j] for i in range(N) for j in range(N) if i<j)}
    return p,("H" if fh>fa else "D" if fh==fa else "A")

def calibration_bins(items):
    bins={}
    for p,a in items:
        conf=max(p.values())
        key=("0.33-0.40" if conf<.40 else "0.40-0.50" if conf<.50 else
             "0.50-0.60" if conf<.60 else "0.60-0.70" if conf<.70 else
             "0.70-0.80" if conf<.80 else "0.80-0.90" if conf<.90 else "0.90-1.00")
        d=bins.setdefault(key,{"n":0,"correct":0,"confidence_sum":0.0})
        d["n"]+=1; d["correct"]+=int(max(p,key=p.get)==a); d["confidence_sum"]+=conf
    return {k:{"n":v["n"],"empirical_hit_rate":round(v["correct"]/v["n"],4),
               "mean_confidence":round(v["confidence_sum"]/v["n"],4),
               "calibration_gap":round(v["confidence_sum"]/v["n"]-v["correct"]/v["n"],4)}
            for k,v in bins.items()}

def blend(p3,p4,w):
    return {k:(1-w)*p3[k]+w*p4[k] for k in ("H","D","A")}

def main():
    con=sqlite3.connect(DB)
    matches=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL
      ORDER BY m.kickoff,m.match_id""").fetchall()

    result={}
    cache=[]
    for model in ("v1","v2","v3","v4"):
        d={"n":0,"correct":0,"brier":0.0,"logloss":0.0}
        items=[]
        for match in matches:
            p,a=run_model(con,model,match); metrics_add(d,p,a); items.append((p,a))
        result[model]=finish(d)
        result[model]["calibration"]=calibration_bins(items)

    # Chronological holdout: choose the V3/V4 blend weight only on the first 60%,
    # then evaluate once on the later 40%. This avoids selecting the blend on its test data.
    split=max(1,int(len(matches)*0.60))
    pair=[]
    for match in matches:
        p3,a=run_model(con,"v3",match)
        p4,_=run_model(con,"v4",match)
        pair.append((p3,p4,a))
    best_w=0.0; best_brier=float("inf")
    for step in range(21):
        w=step/20
        b=0.0
        for p3,p4,a in pair[:split]:
            p=blend(p3,p4,w); y={k:0 for k in p}; y[a]=1
            b+=sum((p[k]-y[k])**2 for k in p)
        b/=split
        if b<best_brier:
            best_brier=b; best_w=w

    hold={}
    for label,fn in [
        ("v3",lambda p3,p4:p3),
        ("v4",lambda p3,p4:p4),
        ("blend",lambda p3,p4:blend(p3,p4,best_w))
    ]:
        d={"n":0,"correct":0,"brier":0.0,"logloss":0.0}
        items=[]
        for p3,p4,a in pair[split:]:
            p=fn(p3,p4); metrics_add(d,p,a); items.append((p,a))
        hold[label]=finish(d); hold[label]["calibration"]=calibration_bins(items)

    result["chronological_holdout"]={
        "train_fraction":0.60,
        "test_fraction":round((len(matches)-split)/len(matches),4),
        "blend_weight_v4":best_w,
        "weight_grid_step":0.05,
        "models":hold
    }

    payload={"definition":"Leakage-safe T-12h comparison. V1 Poisson; V2 Dixon-Coles; V3 adds opponent strength and schedule intent; V4 adds recency-weighted dynamic attack/defence states with opponent-quality adjustment. V4 is experimental. Calibration and V3/V4 blend are selected/evaluated chronologically rather than on the same test slice.","models":result}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False)); con.close()

if __name__=="__main__": main()
