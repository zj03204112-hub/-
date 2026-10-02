import csv, math, sqlite3, json, sys
from datetime import datetime, timedelta
sys.path.insert(0, "data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_draw_gate_boundary_500.csv"
TRAIN_END="2026-08-22T00:00:00"
CLASSES=("H","D","A")

def actual(h,a): return "H" if h>a else "D" if h==a else "A"
def sigmoid(z):
    z=max(-40,min(40,z)); return 1/(1+math.exp(-z))
def softmax(z):
    m=max(z); e=[math.exp(max(-50,min(50,v-m))) for v in z]; s=sum(e); return [v/s for v in e]

def features(con, match):
    mid,kickoff,home,away,fh,fa=match
    ko=datetime.fromisoformat(kickoff[:19]); cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
    p,_=cm.run_model(con,"v4",match)
    lh,la=cm.model_lambdas(con,"v4",match)
    rh=cm.real_strength_metrics(con,home,cutoff); ra=cm.real_strength_metrics(con,away,cutoff)
    gap=rh["score"]-ra["score"]
    # Explicit draw structure: low-score mass + closeness + V4 uncertainty.
    low=math.exp(-(lh+la))*(1+lh*la)
    return [math.log(max(1e-8,p["H"])),math.log(max(1e-8,p["D"])),math.log(max(1e-8,p["A"])),
            p["D"],lh,la,lh+la,abs(lh-la),gap,abs(gap),rh["gd"],ra["gd"],
            rh["stability"],ra["stability"],rh["adj_points"],ra["adj_points"],low,
            min(p.values()),max(p.values())-min(p.values())]

def norm_fit(X):
    d=len(X[0]); mu=[sum(r[j] for r in X)/len(X) for j in range(d)]
    sd=[math.sqrt(sum((r[j]-mu[j])**2 for r in X)/len(X)) or 1 for j in range(d)]
    return mu,sd
def norm(X,mu,sd): return [[(v[j]-mu[j])/sd[j] for j in range(len(v))] for v in X]

def binfit(X,y,weight=1.0,reg=0.01,epochs=1000,lr=.03):
    d=len(X[0])+1; w=[0.0]*d
    for _ in range(epochs):
        g=[0.0]*d
        for x,t in zip(X,y):
            xx=[1.0]+x; q=sigmoid(sum(w[j]*xx[j] for j in range(d)))
            wt=weight if t==1 else 1.0
            for j in range(d): g[j]+=wt*(q-t)*xx[j]
        n=len(X)
        for j in range(d):
            g[j]/=n
            if j: g[j]+=reg*w[j]
            w[j]-=lr*g[j]
    return w
def binpred(w,x): return sigmoid(w[0]+sum(w[j+1]*x[j] for j in range(len(x))))

def hafit(X,Y,reg=.01,epochs=900,lr=.03):
    d=len(X[0])+1; W=[[0.0]*d for _ in ("H","A")]
    for _ in range(epochs):
        g=[[0.0]*d for _ in W]
        for x,t in zip(X,Y):
            xx=[1.0]+x; q=softmax([sum(W[k][j]*xx[j] for j in range(d)) for k in range(2)])
            yi=0 if t=="H" else 1
            for k in range(2):
                e=q[k]-(1 if k==yi else 0)
                for j in range(d): g[k][j]+=e*xx[j]
        n=len(X)
        for k in range(2):
            for j in range(d):
                g[k][j]/=n
                if j: g[k][j]+=reg*W[k][j]
                W[k][j]-=lr*g[k][j]
    return W
def hapred(W,x):
    q=softmax([W[k][0]+sum(W[k][j+1]*x[j] for j in range(len(x))) for k in range(2)])
    return {"H":q[0],"A":q[1]}

def eval_model(draww,haw,X,Y,threshold):
    pred=[]; dc=0; correct=0; pd=0
    for x,y in zip(X,Y):
        d=binpred(draww,x)
        if d>=threshold: p="D"
        else: p=max(hapred(haw,x),key=hapred(haw,x).get)
        pred.append(p); correct+=p==y; pd+=p=="D"; dc+=p=="D" and y=="D"
    ad=sum(y=="D" for y in Y)
    return {"accuracy":correct/len(Y),"correct":correct,"pred_draws":pd,"actual_draws":ad,
            "draw_correct":dc,"draw_recall":dc/ad if ad else 0,"pred":pred}

con=sqlite3.connect(DB)
rows=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
ORDER BY m.kickoff,m.match_id""").fetchall()
with open(MAP,encoding="utf-8-sig",newline="") as f: mp=list(csv.DictReader(f))
if len(mp)!=500 or len({r["match_id"] for r in mp})!=500: raise RuntimeError("FROZEN_500_INVALID")
byid={r[0]:r for r in rows}; frozen=[byid[m["match_id"]] for m in mp]
train=[x for x in rows if x[1][:19] < TRAIN_END]
Xraw=[features(con,x) for x in train]; Y=[actual(x[4],x[5]) for x in train]
split=max(1000,int(len(train)*.75)); Xtr,Ytr=Xraw[:split],Y[:split]; Xva,Yva=Xraw[split:],Y[split:]
mu,sd=norm_fit(Xtr); Xtr=norm(Xtr,mu,sd); Xva=norm(Xva,mu,sd)
draw_y=[int(y=="D") for y in Ytr]; draw_v=[int(y=="D") for y in Yva]
best=None
for weight in (1,1.25,1.5,1.75,2,2.5,3,4,5,6):
    dw=binfit(Xtr,draw_y,weight=weight,reg=.01)
    non=[i for i,y in enumerate(Ytr) if y!="D"]; haw=hafit([Xtr[i] for i in non],[Ytr[i] for i in non],reg=.01)
    for th in (0.25,.27,.29,.31,.33,.35,.37,.39,.41,.43,.45,.47,.49):
        m=eval_model(dw,haw,Xva,Yva,th)
        # Primary accuracy, then draw recall, while penalizing excessive draw inflation.
        inflation=max(0,m["pred_draws"]-2*m["actual_draws"])
        key=(-m["accuracy"],-m["draw_recall"],inflation,abs(m["pred_draws"]-m["actual_draws"]))
        if best is None or key<best[0]: best=(key,weight,th,dw,haw,m)
# Refit selected structure on all pre-frozen history.
mu2,sd2=norm_fit(Xraw); XA=norm(Xraw,mu2,sd2)
dw=binfit(XA,[int(y=="D") for y in Y],weight=best[1],reg=.01,epochs=1200,lr=.03)
non=[i for i,y in enumerate(Y) if y!="D"]; haw=hafit([XA[i] for i in non],[Y[i] for i in non],reg=.01,epochs=1100,lr=.03)
FXraw=[features(con,x) for x in frozen]; FX=norm(FXraw,mu2,sd2); FY=[actual(x[4],x[5]) for x in frozen]
m=eval_model(dw,haw,FX,FY,best[2])
# Baseline exact V4.
base=[]
for x in frozen:
    p,a=cm.run_model(con,"v4",x); base.append((max(p,key=p.get),a,p))
base_correct=sum(p==a for p,a,_ in base)
out=[]
for x,fx,y,(bp,ba,bprob) in zip(frozen,FX,FY,base):
    dp=binpred(dw,fx); hp=hapred(haw,fx); pred="D" if dp>=best[2] else max(hp,key=hp.get)
    out.append({"match_id":x[0],"kickoff":x[1],"home":x[2],"away":x[3],"actual":y,
                "base_pred":bp,"base_H":round(bprob["H"],6),"base_D":round(bprob["D"],6),"base_A":round(bprob["A"],6),
                "draw_probability":round(dp,6),"draw_threshold":best[2],"boundary_H":round(hp["H"],6),"boundary_A":round(hp["A"],6),
                "decision_pred":pred,"changed":pred!=bp,"draw_gate":dp>=best[2]})
with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
report={"train_n":len(train),"selected_draw_weight":best[1],"selected_draw_threshold":best[2],
        "internal_validation":{k:v for k,v in best[5].items() if k!="pred"},
        "frozen_v4_baseline":{"n":500,"correct":base_correct,"accuracy":base_correct/500},
        "frozen_two_stage":{k:v for k,v in m.items() if k!="pred"},
        "hard90_target":{"required_correct":450,"required_accuracy":.90},
        "production_status":"EXPERIMENT_ONLY","output":OUT}
print(json.dumps(report,ensure_ascii=False))
