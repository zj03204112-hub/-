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
      ORDER BY m.kickoff DESC LIMIT 12""",(cutoff,team,team)).fetchall()
    return [((fh,fa) if h==team else (fa,fh), a if h==team else h)
            for _,h,a,fh,fa in rows]

def rates(h):
    if not h: return 1.15,1.15
    w=[math.exp(-0.12*i) for i in range(len(h))]
    sw=sum(w)
    return (max(.2,sum(x[0][0]*w[i] for i,x in enumerate(h))/sw),
            max(.2,sum(x[0][1]*w[i] for i,x in enumerate(h))/sw))

def strength(con,team,before):
    x=hist(con,team,before)[:10]
    if not x: return 0.0
    pts=[]
    for (gf,ga),_ in x: pts.append(3 if gf>ga else 1 if gf==ga else 0)
    return sum(pts)/len(pts)

def tau(i,j,lh,la):
    if i==0 and j==0: return 1-lh*la*RHO
    if i==0 and j==1: return 1+lh*RHO
    if i==1 and j==0: return 1+la*RHO
    if i==1 and j==1: return 1-RHO
    return 1.0

def matrix(lh,la,dc):
    m=[[math.exp(-lh)*lh**i/math.factorial(i)*math.exp(-la)*la**j/math.factorial(j)
        *(tau(i,j,lh,la) if dc else 1.0) for j in range(N)] for i in range(N)]
    s=sum(map(sum,m))
    return [[v/s for v in row] for row in m]

def metrics_add(d,p,actual):
    y={"H":0,"D":0,"A":0}; y[actual]=1
    pred=max(p,key=p.get)
    d["n"]+=1; d["correct"]+=int(pred==actual)
    d["brier"]+=sum((p[k]-y[k])**2 for k in y)
    d["logloss"]-=math.log(max(1e-12,p[actual]))

def finish(d):
    return {"n":d["n"],"accuracy":round(d["correct"]/d["n"],4),
            "brier":round(d["brier"]/d["n"],4),
            "logloss":round(d["logloss"]/d["n"],4)}

def run_model(con,model,match):
    mid,kickoff,home,away,fh,fa=match
    ko=datetime.fromisoformat(kickoff[:19]); cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
    hh=hist(con,home,cutoff); ah=hist(con,away,cutoff)
    hgf,hga=rates(hh); agf,aga=rates(ah)
    if model=="v3":
        hos=strength(con,home,kickoff); aos=strength(con,away,kickoff)
        for side,os in (("home",hos),("away",aos)):
            os=max(.75,min(2.25,os))
            factor=max(.90,min(1.10,(os/1.5)**.25))
            if side=="home": hgf*=factor; hga/=factor
            else: agf*=factor; aga/=factor
        sh=con.execute("SELECT motivation_adjustment FROM schedule_intent WHERE match_id=? AND team_side='home'",(mid,)).fetchone()
        sa=con.execute("SELECT motivation_adjustment FROM schedule_intent WHERE match_id=? AND team_side='away'",(mid,)).fetchone()
        if sh: hgf=max(.2,hgf*(1+float(sh[0] or 0)))
        if sa: agf=max(.2,agf*(1+float(sa[0] or 0)))
    lh=max(.15,min(4.0,.65+.58*hgf+.30*aga))
    la=max(.12,min(3.5,.58+.58*agf+.30*hga))
    m=matrix(lh,la,model!="v1")
    p={"H":sum(m[i][j] for i in range(N) for j in range(N) if i>j),
       "D":sum(m[i][j] for i in range(N) for j in range(N) if i==j),
       "A":sum(m[i][j] for i in range(N) for j in range(N) if i<j)}
    actual="H" if fh>fa else "D" if fh==fa else "A"
    return p,actual

def main():
    con=sqlite3.connect(DB)
    matches=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL
      ORDER BY m.kickoff,m.match_id""").fetchall()
    result={}
    for model in ("v1","v2","v3"):
        d={"n":0,"correct":0,"brier":0.0,"logloss":0.0}
        for match in matches:
            p,a=run_model(con,model,match)
            metrics_add(d,p,a)
        result[model]=finish(d)
    payload={"definition":"Leakage-safe comparison; every model uses kickoff minus 12h and only prior completed results. V3 additionally uses opponent-strength and schedule-intent fields.","models":result}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(json.dumps(payload,ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
