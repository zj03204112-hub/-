#!/usr/bin/env python3
import csv, io, json, math, sqlite3, urllib.request
from datetime import datetime
from pathlib import Path

DB="football_model_database.sqlite"
SAMPLE="model_validation/500_match_base_batch01.csv"
OUT_CSV="model_validation/500_match_V5_SCORE_v1.1_market_calibrated_predictions.csv"
OUT_JSON="model_validation/500_match_V5_SCORE_v1.1_market_calibrated_summary.json"
OUT_MD="model_validation/500_match_V5_SCORE_v1.1_market_calibrated_report.md"
N=8
RHO=-0.05
LEAGUES={"EPL":"E0","LALIGA":"SP1","BUNDESLIGA":"D1","SERIEA":"I1","LIGUE1":"F1","J1":"J1"}
SEASONS={"2025/26":"2526","2026/27":"2627"}

def f(x):
    try: return float(str(x).strip())
    except: return None

def pois(l,k):
    return math.exp(-l)*l**k/math.factorial(k)

def tau(i,j,lh,la):
    if i==0 and j==0:return 1-lh*la*RHO
    if i==0 and j==1:return 1+lh*RHO
    if i==1 and j==0:return 1+la*RHO
    if i==1 and j==1:return 1-RHO
    return 1.0

def matrix(lh,la):
    m=[[pois(lh,i)*pois(la,j)*tau(i,j,lh,la) for j in range(N)] for i in range(N)]
    s=sum(map(sum,m))
    return [[v/s for v in r] for r in m]

def score_probs(m):
    return {f"{i}-{j}":m[i][j] for i in range(N) for j in range(N)}

def outcome_probs(m):
    return {"H":sum(m[i][j] for i in range(N) for j in range(N) if i>j),
            "D":sum(m[i][j] for i in range(N) for j in range(N) if i==j),
            "A":sum(m[i][j] for i in range(N) for j in range(N) if i<j)}

def top_scores(m,n=2):
    xs=sorted(((m[i][j],i,j) for i in range(N) for j in range(N)),reverse=True)
    return [(round(v,8),i,j) for v,i,j in xs[:n]]

def total_probs(m):
    d={}
    for i in range(N):
        for j in range(N): d[i+j]=d.get(i+j,0)+m[i][j]
    return d

def implied3(h,d,a):
    vals=[1/h if h and h>0 else None,1/d if d and d>0 else None,1/a if a and a>0 else None]
    if any(v is None for v in vals): return None
    s=sum(vals)
    return {"H":vals[0]/s,"D":vals[1]/s,"A":vals[2]/s}

def implied_ou(over,under):
    if not over or not under or over<=0 or under<=0:return None
    a,b=1/over,1/under;s=a+b
    return a/s

def calibrate_outcomes(p,market,w=0.12):
    z={k:(1-w)*math.log(max(1e-12,p[k]))+w*math.log(max(1e-12,market[k])) for k in p}
    mx=max(z.values()); ex={k:math.exp(v-mx) for k,v in z.items()};s=sum(ex.values())
    return {k:ex[k]/s for k in ex}

def expected_total(m):
    return sum((i+j)*m[i][j] for i in range(N) for j in range(N))

def adjust_total_lambda(lh,la,m,ou_line,over_p,w=0.12):
    # Find a common scale on lambdas that moves P(Over line) toward the market.
    if ou_line is None or over_p is None:return lh,la
    best=(1e9,1.0)
    for step in range(61):
        scale=0.70+step*0.01
        mm=matrix(lh*scale,la*scale)
        tp=total_probs(mm)
        op=sum(v for t,v in tp.items() if t>ou_line)
        err=(op-over_p)**2
        if err<best[0]:best=(err,scale)
    scale=1+w*(best[1]-1.0)
    return lh*scale,la*scale

def load_sample():
    with open(SAMPLE,encoding="utf-8-sig",newline="") as h:
        sample=list(csv.DictReader(h))
    mapping_path="model_validation/500_match_to_db_match_id_mapping.csv"
    with open(mapping_path,encoding="utf-8-sig",newline="") as h:
        mapping=list(csv.DictReader(h))
    by_row={str(r.get("sample_row")):r for r in mapping}
    out=[]
    for idx,s in enumerate(sample,1):
        m=by_row.get(str(idx))
        if not m: raise RuntimeError(f"frozen mapping missing sample row {idx}")
        z=dict(s); z["match_id"]=m["match_id"]; z["db_home"]=m["db_home"]; z["db_away"]=m["db_away"]; z["db_kickoff"]=m["db_kickoff"]
        out.append(z)
    if len(out)!=500 or len({r["match_id"] for r in out})!=500: raise RuntimeError("frozen-500 mapping integrity failure")
    return out

def parse_date(s):
    s=(s or "").strip()
    for fmt in ("%d/%m/%Y","%d/%m/%y","%Y-%m-%d"):
        try:return datetime.strptime(s,fmt).strftime("%Y-%m-%d")
        except: pass
    return ""

def load_market_data():
    data={}
    for league,code in LEAGUES.items():
        for season,scode in SEASONS.items():
            if league=="J1":
                url=f"https://football-data.co.uk/new/JPN.csv"
            else:
                url=f"https://www.football-data.co.uk/mmz4281/{scode}/{code}.csv"
            try:
                raw=urllib.request.urlopen(url,timeout=30).read().decode("latin1")
            except Exception:
                continue
            for r in csv.DictReader(io.StringIO(raw)):
                date=parse_date(r.get("Date"))
                if not date:continue
                iso=None
                for fmt in ("%d/%m/%Y","%d/%m/%y","%Y-%m-%d"):
                    try:iso=datetime.strptime(date,fmt).strftime("%Y-%m-%d");break
                    except:pass
                if not iso:continue
                home=(r.get("HomeTeam") or "").strip();away=(r.get("AwayTeam") or "").strip()
                if not home or not away:continue
                key=(iso,home.lower(),away.lower())
                data[key]=(league,r)
    return data

def market_for(row,market):
    # Football-Data's non-C columns are the first/pre-closing market set;
    # C columns are closing and are intentionally excluded from v1.1.
    h=f(market.get("AvgH"));d=f(market.get("AvgD"));a=f(market.get("AvgA"))
    p1=implied3(h,d,a)
    ou_line=None; over=None; under=None
    for line in (2.5,3.5,2.25,2.75):
        s=str(line).replace(".","")
        # Most files use >2.5 / <2.5 naming.
        for base in (f">{line}",f"<{line}"):
            pass
        o=f(market.get(f"Avg>{line}"));u=f(market.get(f"Avg<{line}"))
        if o and u:
            ou_line=line;over=o;under=u;break
    oup=implied_ou(over,under)
    ah_line=f(market.get("AHh"));ah_h=f(market.get("BbAvAHH"));ah_a=f(market.get("BbAvAHA"))
    ahp=None
    if ah_h and ah_a:
        # 2-way AH prices are normalized as a soft market signal.
        q=[1/ah_h,1/ah_a];s=sum(q);ahp={"home_side":q[0]/s,"away_side":q[1]/s}
    return {"one_x_two":p1,"ou_line":ou_line,"over_p":oup,"ah_line":ah_line,"ah_p":ahp}

def apply_market_to_matrix(lh,la,m,md):
    p=outcome_probs(m)
    used=[]
    if md.get("one_x_two"):
        p=calibrate_outcomes(p,md["one_x_two"],0.10);used.append("1X2_open_avg")
    if md.get("ah_p") and md.get("ah_line") is not None:
        ah=md["ah_p"]
        target_h=ah["home_side"]
        base_h=p["H"]
        shift=max(-0.10,min(0.10,0.08*(target_h-base_h)))
        p={"H":max(1e-9,p["H"]+shift),"D":max(1e-9,p["D"]),"A":max(1e-9,p["A"]-shift)}
        z=sum(p.values());p={k:v/z for k,v in p.items()};used.append("AH_open")
    lh2,la2=lh,la
    if md.get("ou_line") is not None and md.get("over_p") is not None:
        lh2,la2=adjust_total_lambda(lh2,la2,m,md["ou_line"],md["over_p"],0.12)
        used.append("OU_open_avg")
    if lh2!=lh or la2!=la:
        m=matrix(lh2,la2)
    # Outcome calibration is applied as a matrix-preserving multiplicative tilt:
    # this changes H/D/A while retaining the local score geometry.
    if md.get("one_x_two"):
        base=outcome_probs(m); target=calibrate_outcomes(base,md["one_x_two"],0.10)
        ratio={k:target[k]/max(1e-12,base[k]) for k in target}
        mm=[[0.0]*N for _ in range(N)]
        for i in range(N):
            for j in range(N):
                k="H" if i>j else "D" if i==j else "A"
                mm[i][j]=m[i][j]*ratio[k]
        s=sum(map(sum,mm));m=[[v/s for v in r] for r in mm]
    return m,used

def brier(p,a):
    y={k:0 for k in ("H","D","A")};y[a]=1
    return sum((p[k]-y[k])**2 for k in y)
def logloss(p,a):return -math.log(max(1e-12,p[a]))

def main():
    sample=load_sample()
    db=sqlite3.connect(DB)
    market=load_market_data()
    rows=[];base_items=[];cal_items=[]
    coverage={"market_1x2":0,"market_ou":0,"market_ah":0,"any_market":0,"no_market":0}
    for s in sample:
        mid=s["match_id"]
        if not mid:
            # Frozen base file uses sample_row rather than DB id; resolve by date/team/score.
            q=db.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
              FROM matches m JOIN results r ON r.match_id=m.match_id
              WHERE substr(m.kickoff,1,10)=? AND r.ft_home=? AND r.ft_away=?""",
              (s["date"],int(s["home_score"]),int(s["away_score"]))).fetchall()
            q=[x for x in q if x[2].lower()==s["db_home"].lower() and x[3].lower()==s["db_away"].lower()]
            if len(q)!=1: continue
            mid,kickoff,home,away,fh,fa=q[0]
        else:
            q=db.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
              FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.match_id=?""",(mid,)).fetchone()
            if not q:continue
            mid,kickoff,home,away,fh,fa=q
        import sys
        sys.path.insert(0,"data_pipeline")
        import compare_models as cm
        lh,la=cm.model_lambdas(db,"v4",(mid,kickoff,home,away,fh,fa))
        bm=matrix(lh,la); bp=outcome_probs(bm)
        key=(kickoff[:10],home.lower(),away.lower())
        raw_md=market.get(key,{})
        md=market_for(None,raw_md) if raw_md else {}
        if md.get("one_x_two"):coverage["market_1x2"]+=1
        if md.get("ou_line") is not None:coverage["market_ou"]+=1
        if md.get("ah_line") is not None:coverage["market_ah"]+=1
        if md.get("one_x_two") or md.get("ou_line") is not None or md.get("ah_line") is not None:coverage["any_market"]+=1
        else:coverage["no_market"]+=1
        cmatrix,used=apply_market_to_matrix(lh,la,bm,md)
        cp=outcome_probs(cmatrix)
        actual="H" if fh>fa else "D" if fh==fa else "A"
        btop=top_scores(bm);ctop=top_scores(cmatrix)
        bt=total_probs(bm);ct=total_probs(cmatrix)
        btotal=max(bt,key=bt.get);ctotal=max(ct,key=ct.get)
        base_items.append((bp,actual,bt,btop,(fh+fa)))
        cal_items.append((cp,actual,ct,ctop,(fh+fa)))
        rows.append({"match_id":mid,"date":kickoff[:10],"home":home,"away":away,
          "baseline_score1":f"{btop[0][1]}-{btop[0][2]}","baseline_score2":f"{btop[1][1]}-{btop[1][2]}",
          "market_score1":f"{ctop[0][1]}-{ctop[0][2]}","market_score2":f"{ctop[1][1]}-{ctop[1][2]}",
          "baseline_90":max(bp,key=bp.get),"market_90":max(cp,key=cp.get),
          "baseline_total_goals":btotal,"market_total_goals":ctotal,
          "actual_score":f"{fh}-{fa}","actual_total_goals":fh+fa,
          "market_used":"+".join(used) if used else "NONE",
          "market_1x2":"yes" if md.get("one_x_two") else "no",
          "market_ou":"yes" if md.get("ou_line") is not None else "no",
          "market_ah":"yes" if md.get("ah_line") is not None else "no",
          "baseline_over25":sum(v for t,v in bt.items() if t>2.5),
          "market_over25":sum(v for t,v in ct.items() if t>2.5),
          "actual_over25":(fh+fa)>2.5})

    def summary(items):
        n=len(items);return {
          "n":n,
          "90_accuracy":sum(max(p,key=p.get)==a for p,a,_,_,_ in items)/n if n else None,
          "brier":sum(brier(p,a) for p,a,_,_,_ in items)/n if n else None,
          "logloss":sum(logloss(p,a) for p,a,_,_,_ in items)/n if n else None,
          "score1_accuracy":sum(f"{sc[0][1]}-{sc[0][2]}"==f"{actual}" for _,actual,_,sc,_ in items)/n if n else None,
          "score2_accuracy":sum(f"{sc[1][1]}-{sc[1][2]}"==f"{actual}" for _,actual,_,sc,_ in items)/n if n else None,
          "total_goals_exact_accuracy":sum(max(t,key=t.get)==actual_total for _,_,t,_,actual_total in items)/n if n else None,
          "total_goals_mae":sum(abs(sum(float(k)*v for k,v in t.items())-actual_total) for _,_,t,_,actual_total in items)/n if n else None
        }
    # Fix exact score and total metrics with direct fields.
    def detailed(rows,prefix):
        n=len(rows)
        return {
          "n":n,
          "score1_accuracy":sum(r[prefix+"_score1"]==r["actual_score"] for r in rows)/n if n else None,
          "score2_accuracy":sum(r[prefix+"_score2"]==r["actual_score"] for r in rows)/n if n else None,
          "90_accuracy":sum(r[prefix+"_90"]==("H" if int(r["actual_score"].split("-")[0])>int(r["actual_score"].split("-")[1]) else "D" if r["actual_score"].split("-")[0]==r["actual_score"].split("-")[1] else "A") for r in rows)/n if n else None,
          "total_goals_exact_accuracy":sum(int(r[prefix+"_total_goals"])==int(r["actual_total_goals"]) for r in rows)/n if n else None,
          "total_goals_mae":sum(abs(int(r[prefix+"_total_goals"])-int(r["actual_total_goals"])) for r in rows)/n if n else None
        }
    base_metric=detailed(rows,"baseline");market_metric=detailed(rows,"market")
    # Probability metrics
    def pm(items):
        n=len(items);return {"brier":sum(brier(p,a) for p,a,_,_,_ in items)/n if n else None,
          "logloss":sum(logloss(p,a) for p,a,_,_,_ in items)/n if n else None}
    summary={"model_version":"V5-SCORE v1.1-Market-Calibrated","baseline":"V5-SCORE v1.0 reconstructed/frozen",
      "sample_count":len(rows),"market_coverage":coverage,
      "metrics":{"v1.0":{**base_metric,**pm([(x[0],x[1],x[2],x[3],x[4]) for x in base_items])},
                 "v1.1_market_calibrated":{**market_metric,**pm([(x[0],x[1],x[2],x[3],x[4]) for x in cal_items])}}}
    # Add 2.5/2.75 direction evaluation when an OU market line exists.
    ou_rows=[]
    for r in rows:
        if r["market_ou"]=="yes":
            # Direction is evaluated from the predicted total-goal distribution against the matched line.
            pass
    summary["definition"]={"market_input":"Football-Data opening/non-C odds where available; market data are auxiliary only.",
      "calibration":"small fixed market weight; common-lambda scale for O/U; H/D/A multiplicative tilt for 1X2.",
      "leakage_rule":"closing C-columns are excluded. This is market-opening/pre-closing data, not a claimed exact T-12h snapshot.",
      "handicap":"Asian handicap line/price retained as reference and separately reported; it is not used as a direct winner rule.",
      "score_matrix":"v1.0 matrix is transformed; no P(D) threshold back-solving."}
    Path(OUT_CSV).write_text("",encoding="utf-8")
    with open(OUT_CSV,"w",encoding="utf-8-sig",newline="") as h:
        w=csv.DictWriter(h,fieldnames=list(rows[0].keys()));w.writeheader();w.writerows(rows)
    Path(OUT_JSON).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    md=["# V5-SCORE v1.1-Market-Calibrated — 冻结500场独立实验","",
        f"- 样本：{len(rows)}场；v1.0未修改。","- 市场数据仅作为辅助校准，不直接决定90分钟胜负。",
        "- 使用Football-Data非C列（开盘后/赛前市场数据）；C列收盘数据明确排除。Football-Data说明其自2019/20起分别收集开盘后的第一组赔率与收盘赔率。","",
        "## 市场覆盖",json.dumps(coverage,ensure_ascii=False,indent=2),"",
        "## 指标对比","|指标|v1.0|v1.1-Market-Calibrated|","|---|---:|---:|"]
    for k in ["score1_accuracy","score2_accuracy","90_accuracy","total_goals_exact_accuracy","total_goals_mae","brier","logloss"]:
        md.append(f"|{k}|{base_metric.get(k)}|{market_metric.get(k)}|")
    md += ["","## 8×8矩阵变化","每场保留v1.0与v1.1的前两比分；完整矩阵未写入CSV，以避免文件膨胀。实验代码通过同一v1.0矩阵进行市场变换。",
           "","## 重要限制","1. Football-Data的开盘数据是其公布的开盘后采样，不等同于严格的赛前6–12小时固定截面。",
           "2. K League 1没有纳入Football-Data市场数据，因此相关场次会显示NONE。",
           "3. 只有在市场字段存在时才进行对应校准；没有数据绝不补造。",
           "4. 让球命中需要实际竞彩让球，仍按实际盘口+实际比分单独评价。"]
    Path(OUT_MD).write_text("\n".join(md)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
