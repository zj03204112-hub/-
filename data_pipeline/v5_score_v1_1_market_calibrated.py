import csv, json, math, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import sys
sys.path.insert(0, "data_pipeline")
import compare_models as cm

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"football_model_database.sqlite"
MAP=ROOT/"model_validation/500_match_to_db_match_id_mapping.csv"
OUT=ROOT/"model_validation/v5_score_v1_1_market_calibrated_result.json"

MARKET_TABLE="market_snapshot"
REQUIRED=("asian_line","asian_home_odds","asian_away_odds","euro_home_odds","euro_draw_odds","euro_away_odds","ou_line","over_odds","under_odds")

def probs(m):
    n=cm.N
    return {"H":sum(m[i][j] for i in range(n) for j in range(n) if i>j),
            "D":sum(m[i][j] for i in range(n) for j in range(n) if i==j),
            "A":sum(m[i][j] for i in range(n) for j in range(n) if i<j)}

def score_cells(m):
    cells=[]
    for i,row in enumerate(m):
        for j,p in enumerate(row):
            cells.append((p,i,j))
    cells.sort(reverse=True)
    return cells[:2]

def brier(p,a):
    y={k:float(k==a) for k in ("H","D","A")}
    return sum((p[k]-y[k])**2 for k in y)

def logloss(p,a):
    return -math.log(max(1e-12,p[a]))

def implied3(h,d,a):
    vals=[1/h,1/d,1/a]
    s=sum(vals)
    return [x/s for x in vals]

def implied_ou(over,under):
    vals=[1/over,1/under]; s=sum(vals)
    return vals[0]/s

def apply_market(lh,la,row,params):
    # Market is a bounded calibration signal, never the primary model.
    total=lh+la
    diff=lh-la
    euro_h,euro_d,euro_a=implied3(row["euro_home_odds"],row["euro_draw_odds"],row["euro_away_odds"])
    market_diff=(euro_h-euro_a)
    asian=row["asian_line"]
    asian_signal=-float(asian)
    ou_prob=implied_ou(row["over_odds"],row["under_odds"])
    ou_signal=2*ou_prob-1
    total_signal=(float(row["ou_line"])-total)
    nd=params
    new_total=total + nd["total_coef"]*total_signal + nd["ou_coef"]*ou_signal
    new_diff=diff + nd["diff_coef"]*(market_diff + nd["asian_coef"]*asian_signal)
    new_total=max(0.30,min(5.50,new_total))
    new_diff=max(-3.50,min(3.50,new_diff))
    nh=max(.10,min(4.5,(new_total+new_diff)/2))
    na=max(.10,min(4.0,(new_total-new_diff)/2))
    return nh,na

def metrics(rows,key):
    n=len(rows)
    if not n:return {"n":0}
    exact1=sum(r["actual_score"]==r[key][0] for r in rows)
    exact2=sum(r["actual_score"]==r[key][1] for r in rows)
    hda=sum(r["actual_result"]==max(r[key+"_p"],key=r[key+"_p"].get) for r in rows)
    return {
      "n":n,"score1_hit_rate":exact1/n,"score2_hit_rate":exact2/n,
      "hda_accuracy":hda/n,
      "brier":sum(r[key+"_brier"] for r in rows)/n,
      "logloss":sum(r[key+"_logloss"] for r in rows)/n
    }

con=sqlite3.connect(DB)
tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
with open(MAP,encoding="utf-8-sig") as f: mp=list(csv.DictReader(f))
assert len(mp)==500 and len({x["match_id"] for x in mp})==500 and all(x["mapping_status"]=="unique" for x in mp)

coverage={"sample_n":500,"market_table_present":MARKET_TABLE in tables,"complete_market_n":0,"missing_n":500,"required_fields":list(REQUIRED)}
if MARKET_TABLE not in tables:
    OUT.write_text(json.dumps({"status":"BLOCKED_MARKET_DATA","coverage":coverage,
      "reason":"冻结500场当前数据库没有完整T-12h欧赔+亚盘+大小球水位快照表；未运行伪造的500场v1.1结果。"},ensure_ascii=False,indent=2))
    print(json.dumps({"status":"BLOCKED_MARKET_DATA","coverage":coverage},ensure_ascii=False))
    raise SystemExit(0)

cols={r[1] for r in con.execute(f"PRAGMA table_info({MARKET_TABLE})")}
if not set(REQUIRED)<=cols:
    coverage["available_columns"]=sorted(cols)
    OUT.write_text(json.dumps({"status":"BLOCKED_MARKET_SCHEMA","coverage":coverage,
      "reason":"市场快照表缺少完整的亚盘/欧赔/大小球字段；不降级伪造实验。"},ensure_ascii=False,indent=2))
    print(json.dumps({"status":"BLOCKED_MARKET_SCHEMA","coverage":coverage},ensure_ascii=False))
    raise SystemExit(0)

db={}
for r in con.execute("SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away FROM matches m JOIN results r ON r.match_id=m.match_id WHERE r.ft_home IS NOT NULL"):
    db[r[0]]=r

rows=[]
for z in mp:
    r=db[z["match_id"]]
    ko=datetime.fromisoformat(r[1][:19]); cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
    q=con.execute(f"""SELECT {",".join(REQUIRED)} FROM {MARKET_TABLE}
                      WHERE match_id=? AND captured_at<=?
                      ORDER BY captured_at DESC LIMIT 1""",(r[0],cutoff)).fetchone()
    if not q or any(v in (None,"") for v in q):
        continue
    market=dict(zip(REQUIRED,q))
    lh,la=cm.model_lambdas(con,"v4",r)
    base=cm.matrix(lh,la,True)
    nh,na=apply_market(lh,la,market,{"total_coef":0.25,"ou_coef":0.10,"diff_coef":0.25,"asian_coef":0.25})
    cal=cm.matrix(nh,na,True)
    p0=probs(base); p1=probs(cal)
    a="H" if r[4]>r[5] else "D" if r[4]==r[5] else "A"
    s0=score_cells(base); s1=score_cells(cal)
    actual=f"{r[4]}-{r[5]}"
    rows.append({"match_id":r[0],"actual_score":actual,"actual_result":a,
      "v1_score":[f"{x[1]}-{x[2]}" for x in s0],"v1_p":p0,
      "v11_score":[f"{x[1]}-{x[2]}" for x in s1],"v11_p":p1,
      "v1_brier":brier(p0,a),"v1_logloss":logloss(p0,a),
      "v11_brier":brier(p1,a),"v11_logloss":logloss(p1,a),
      "matrix_l1_delta":sum(abs(base[i][j]-cal[i][j]) for i in range(cm.N) for j in range(cm.N)),
      "lambda_before":[lh,la],"lambda_after":[nh,na]})

coverage["complete_market_n"]=len(rows); coverage["missing_n"]=500-len(rows)
if len(rows)<400:
    payload={"status":"BLOCKED_INSUFFICIENT_COMPLETE_MARKET_COVERAGE","coverage":coverage,
             "reason":"完整T-12h市场快照少于400/500；不把部分样本冒充完整500场独立实验。","rows":rows}
else:
    split=int(len(rows)*.60)
    train,test=rows[:split],rows[split:]
    payload={"status":"READY_FOR_EVALUATION","coverage":coverage,
      "split":{"train_n":len(train),"test_n":len(test)},
      "baseline_test_metrics":metrics(test,"v1"),
      "v11_test_metrics":metrics(test,"v11"),
      "mean_matrix_l1_delta":sum(r["matrix_l1_delta"] for r in test)/len(test),
      "rows":rows}

OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in payload.items() if k!="rows"},ensure_ascii=False))
