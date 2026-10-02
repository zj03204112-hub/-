#!/usr/bin/env python3
import csv,json,math,sqlite3,io,urllib.request,re
from pathlib import Path
from datetime import datetime,timedelta
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"football_model_database.sqlite"
SAMPLE=ROOT/"model_validation/500_match_base_batch01.csv"
COV=ROOT/"model_validation/V5-SCORE_v1.1_market_data_coverage.csv"
OUT=ROOT/"model_validation/V5-SCORE_v1.1_vs_v1.0_500.csv"
JSONOUT=ROOT/"model_validation/V5-SCORE_v1.1_vs_v1.0_500.json"
MATRIXOUT=ROOT/"model_validation/V5-SCORE_v1.1_matrix_change_500.csv"
sys_path=str(ROOT/"data_pipeline")
import sys
if sys_path not in sys.path: sys.path.insert(0,sys_path)
import compare_models as cm
N=8

def norm(s): return re.sub(r"[^a-z0-9]","",(s or "").lower())
def probs_market(h,d,a):
    q=[1.0/x for x in (h,d,a)]; z=sum(q); return [x/z for x in q]
def matrix_probs(m):
    ph=sum(m[i][j] for i in range(N) for j in range(N) if i>j)
    pd=sum(m[i][j] for i in range(N) for j in range(N) if i==j)
    pa=sum(m[i][j] for i in range(N) for j in range(N) if i<j)
    return {"H":ph,"D":pd,"A":pa}
def total_probs(m):
    p=defaultdict(float)
    for i,row in enumerate(m):
        for j,v in enumerate(row): p[i+j]+=v
    return dict(p)
def over_prob(m,line):
    return sum(v for i,row in enumerate(m) for j,v in enumerate(row) if i+j>line)
def ah_probs(m,line):
    d={"H":0.0,"D":0.0,"A":0.0}
    for i,row in enumerate(m):
        for j,v in enumerate(row):
            z=i+line-j
            d["H" if z>0 else "D" if abs(z)<1e-9 else "A"]+=v
    return d
def apply_cal(m,q,qo,alpha,beta):
    base=matrix_probs(m)
    keys=["H","D","A"]
    # bounded multiplicative market evidence on outcome class and total-goal side.
    out=[]
    for i,row in enumerate(m):
        nr=[]
        for j,v in enumerate(row):
            k="H" if i>j else "D" if i==j else "A"
            bo=base[k]; w=math.exp(alpha*math.log(max(1e-12,q[keys.index(k)])/max(1e-12,bo)))
            bt=over_prob(m,2.5)
            is_over=(i+j)>2.5
            qt=qo if is_over else (1-qo)
            pt=bt if is_over else (1-bt)
            w*=math.exp(beta*math.log(max(1e-12,qt)/max(1e-12,pt)))
            nr.append(v*w)
        out.append(nr)
    s=sum(map(sum,out)); return [[v/s for v in r] for r in out]
def score_nll(m,fh,fa):
    return -math.log(max(1e-12,m[fh][fa]))
def brier3(p,actual):
    y={"H":0,"D":0,"A":0}; y[actual]=1
    return sum((p[k]-y[k])**2 for k in y)
def direction(fh,fa): return "H" if fh>fa else "D" if fh==fa else "A"
def top2(m):
    xs=sorted(((m[i][j],i,j) for i in range(N) for j in range(N)),reverse=True)
    return [(x[1],x[2]) for x in xs[:2]]
def actual_ah(fh,fa,line):
    z=fh+line-fa
    return "H" if z>0 else "D" if abs(z)<1e-9 else "A"
def actual_ou(total,line):
    # For 2.5/2.75, direction is simply >=3 vs <=2.
    return "O" if total>line else "U"
def load_cov():
    with COV.open(encoding="utf-8-sig",newline="") as f:return {r["sample_row"]:r for r in csv.DictReader(f)}
def load_samples():
    with SAMPLE.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def main():
    cov=load_cov(); samples=load_samples()
    con=sqlite3.connect(DB)
    rows=[]
    for s in samples:
        mid=s["match_id"]; mrow=con.execute("""SELECT match_id,kickoff,home_team,away_team,? AS fh,? AS fa
          FROM matches WHERE match_id=?""",(int(s["home_score"]),int(s["away_score"]),mid)).fetchone()
        if not mrow: raise RuntimeError(f"missing DB match {mid}")
        base_tuple=mrow
        lh,la=cm.model_lambdas(con,"v4",base_tuple)
        base=cm.matrix(lh,la,True)
        c=cov[mid]
        market_ok=bool(c["all_three_complete"]=="True")
        q=None; qo=None
        if market_ok:
            qv=probs_market(float(c["euro_h"]),float(c["euro_d"]),float(c["euro_a"]))
            q={"H":qv[0],"D":qv[1],"A":qv[2]}
            qo=1/float(c["ou_over"]); qu=1/float(c["ou_under"]); qo=qo/(qo+qu)
        actual=direction(int(s["home_score"]),int(s["away_score"]))
        rows.append({"s":s,"mid":mid,"base":base,"market_ok":market_ok,"q":q,"qo":qo,"actual":actual,
                     "fh":int(s["home_score"]),"fa":int(s["away_score"]),"c":c})
    train=rows[:int(len(rows)*0.60)]
    test=rows[int(len(rows)*0.60):]
    best=(0,0,float("inf"))
    for a in [0,.15,.30,.45,.60,.75,1.0]:
        for b in [0,.15,.30,.45,.60,.75,1.0]:
            vals=[]
            for r in train:
                if not r["market_ok"]: continue
                mm=apply_cal(r["base"],r["q"],r["qo"],a,b)
                vals.append(score_nll(mm,r["fh"],r["fa"]))
            if vals:
                z=sum(vals)/len(vals)
                if z<best[2]: best=(a,b,z)
    alpha,beta,_=best
    detail=[]; matrix_rows=[]
    agg=defaultdict(float); n=0
    for r in rows:
        b=r["base"]; c=r["c"]
        v1p=matrix_probs(b); t1=total_probs(b); s1=top2(b)[0]; s2=top2(b)[1]
        if r["market_ok"]:
            v11=apply_cal(b,r["q"],r["qo"],alpha,beta)
        else:
            v11=b
        p=matrix_probs(v11); tt=total_probs(v11); ss=top2(v11)[0]; ss2=top2(v11)[1]
        row={"sample_row":r["mid"],"date":r["s"]["date"],"league":r["s"]["league"],"home":r["s"]["home"],"away":r["s"]["away"],
             "actual_score":f'{r["fh"]}-{r["fa"]}',"market_complete":r["market_ok"],
             "v1_score1":f"{s1[0]}-{s1[1]}","v1_score2":f"{s2[0]}-{s2[1]}",
             "v11_score1":f"{ss[0]}-{ss[1]}","v11_score2":f"{ss2[0]}-{ss2[1]}",
             "v1_90":max(v1p,key=v1p.get),"v11_90":max(p,key=p.get),
             "v1_total_mode":max(t1,key=t1.get),"v11_total_mode":max(tt,key=tt.get),
             "v1_total_mean":round(sum(k*v for k,v in t1.items()),4),"v11_total_mean":round(sum(k*v for k,v in tt.items()),4)}
        for tag,mm,pp in [("v1",b,v1p),("v11",v11,p)]:
            row[f"{tag}_90_hit"]=int(max(pp,key=pp.get)==r["actual"])
            row[f"{tag}_score1_hit"]=int((s1 if tag=="v1" else ss)==(r["fh"],r["fa"]))
            row[f"{tag}_score2_hit"]=int((s2 if tag=="v1" else ss2)==(r["fh"],r["fa"]))
            tm=t1 if tag=="v1" else tt
            row[f"{tag}_total_hit"]=int(max(tm,key=tm.get)==r["fh"]+r["fa"])
            agg[f"{tag}_brier"]+=brier3(pp,r["actual"])
            agg[f"{tag}_logloss"]+=-math.log(max(1e-12,pp[r["actual"]]))
        # Market evaluation only where the relevant opening market exists.
        if c["ah_complete"]=="True":
            line=float(c["ah_line"]); a1=actual_ah(r["fh"],r["fa"],line)
            for tag,mm in [("v1",b),("v11",v11)]:
                ap=ah_probs(mm,line); pred=max(ap,key=ap.get)
                row[f"{tag}_ah_pred"]=pred; row[f"{tag}_ah_hit"]=int(pred==a1)
                agg[f"{tag}_ah_n"]+=1; agg[f"{tag}_ah_brier"]+=brier3(ap,a1); agg[f"{tag}_ah_logloss"]+=-math.log(max(1e-12,ap[a1]))
        for line in (2.5,2.75):
            if c["ou_complete"]=="True" and abs(float(c["ou_line"])-line)<1e-9:
                actual_o=actual_ou(r["fh"]+r["fa"],line)
                for tag,mm in [("v1",b),("v11",v11)]:
                    op=over_prob(mm,line); pred="O" if op>.5 else "U"
                    row[f"{tag}_ou{str(line).replace('.','')}_pred"]=pred
                    row[f"{tag}_ou{str(line).replace('.','')}_hit"]=int(pred==actual_o)
                    agg[f"{tag}_ou{line}_n"]+=1; agg[f"{tag}_ou{line}_brier"]+=(op-(1 if actual_o=="O" else 0))**2
        detail.append(row)
        for i in range(N):
            for j in range(N):
                matrix_rows.append({"sample_row":r["mid"],"i":i,"j":j,"v1":round(b[i][j],8),"v11":round(v11[i][j],8),"delta":round(v11[i][j]-b[i][j],8),"market_complete":r["market_ok"]})
        n+=1
    fields=list(detail[0].keys())
    with OUT.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(detail)
    with MATRIXOUT.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=matrix_rows[0].keys());w.writeheader();w.writerows(matrix_rows)
    def avg(k): return round(agg[k]/n,6) if n else None
    report={"version":"V5-SCORE v1.1-Market-Calibrated","frozen_n":len(rows),"train_n":len(train),"test_n":len(test),
            "fit":{"alpha_outcome":alpha,"beta_total":beta,"train_exact_score_nll":round(best[2],6)},
            "market_complete_n":sum(r["market_ok"] for r in rows),
            "metrics_500_replay":{
              "v1.0":{"score1_hit_rate":round(sum(x["v1_score1_hit"] for x in detail)/n,4),"score2_hit_rate":round(sum(x["v1_score2_hit"] for x in detail)/n,4),"total_mode_hit_rate":round(sum(x["v1_total_hit"] for x in detail)/n,4),"90_accuracy":round(sum(x["v1_90_hit"] for x in detail)/n,4),"brier":avg("v1_brier"),"logloss":avg("v1_logloss")},
              "v1.1":{"score1_hit_rate":round(sum(x["v11_score1_hit"] for x in detail)/n,4),"score2_hit_rate":round(sum(x["v11_score2_hit"] for x in detail)/n,4),"total_mode_hit_rate":round(sum(x["v11_total_hit"] for x in detail)/n,4),"90_accuracy":round(sum(x["v11_90_hit"] for x in detail)/n,4),"brier":avg("v11_brier"),"logloss":avg("v11_logloss")}},
            "market_metrics":{"note":"Only rows with the corresponding opening market are scored; no claim of 500 coverage unless n=500.",
                              "ah":{"n":int(agg["v1_ah_n"]),"v1_accuracy":round(sum(x.get("v1_ah_hit",0) for x in detail)/max(1,int(agg["v1_ah_n"])),4),"v11_accuracy":round(sum(x.get("v11_ah_hit",0) for x in detail)/max(1,int(agg["v11_ah_n"])),4),"v1_brier":round(agg["v1_ah_brier"]/max(1,agg["v1_ah_n"]),6),"v11_brier":round(agg["v11_ah_brier"]/max(1,agg["v11_ah_n"]),6)},
                              "ou25":{"n":int(agg["v1_ou2.5_n"]),"v1_accuracy":round(sum(x.get("v1_ou25_hit",0) for x in detail)/max(1,int(agg["v1_ou2.5_n"])),4),"v11_accuracy":round(sum(x.get("v11_ou25_hit",0) for x in detail)/max(1,int(agg["v11_ou2.5_n"])),4)},
                              "ou275":{"n":int(agg["v1_ou2.75_n"]),"v1_accuracy":round(sum(x.get("v1_ou275_hit",0) for x in detail)/max(1,int(agg["v1_ou2.75_n"])),4),"v11_accuracy":round(sum(x.get("v11_ou275_hit",0) for x in detail)/max(1,int(agg["v11_ou2.75_n"])),4)}},
            "matrix_change":{"mean_abs_delta":round(sum(abs(x["delta"]) for x in matrix_rows)/len(matrix_rows),8),
                             "mean_signed_delta":round(sum(x["delta"] for x in matrix_rows)/len(matrix_rows),8)}}
    JSONOUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
if __name__=="__main__": main()
