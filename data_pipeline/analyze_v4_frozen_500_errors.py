import csv, json, math
from collections import Counter,defaultdict

IN="model_validation/500_match_V4_frozen_predictions.csv"
OUTJ="model_validation/500_match_V4_error_diagnosis.json"
OUTM="model_validation/500_match_V4_error_diagnosis.md"

rows=list(csv.DictReader(open(IN,encoding="utf-8")))
if len(rows)!=500: raise RuntimeError(f"expected 500 rows, got {len(rows)}")

def pct(n,d): return round(100*n/d,2) if d else 0.0
def confusion(sub):
    c=defaultdict(int)
    for r in sub: c[(r["actual_result"],r["pred_result"])]+=1
    return {a:{p:c[(a,p)] for p in "HDA"} for a in "HDA"}

errors=[r for r in rows if r["actual_result"]!=r["pred_result"]]
err_by_pred=Counter(r["pred_result"] for r in errors)
err_by_actual=Counter(r["actual_result"] for r in errors)

# Strength-gap buckets use frozen pre-match V4 real-strength score gap.
def bucket(g):
    if g<0.10:return "小(<0.10)"
    if g<0.20:return "中(0.10-0.20)"
    return "大(>=0.20)"
sb=defaultdict(lambda:[0,0])
for r in rows:
    b=bucket(float(r["strength_gap"])); sb[b][0]+=1
    sb[b][1]+=int(r["actual_result"]!=r["pred_result"])
strength_gap={k:{"n":v[0],"errors":v[1],"error_rate_pct":pct(v[1],v[0])} for k,v in sb.items()}

# Net-goal error concentration
gd_bins=defaultdict(lambda:[0,0])
for r in rows:
    e=float(r["goal_diff_abs_error"])
    b="0-0.49" if e<0.5 else "0.50-0.99" if e<1 else "1.00-1.49" if e<1.5 else "1.50-1.99" if e<2 else "2.00-2.99" if e<3 else ">=3.00"
    gd_bins[b][0]+=1; gd_bins[b][1]+=int(r["actual_result"]!=r["pred_result"])
net_goal_error={k:{"n":v[0],"errors":v[1],"error_rate_pct":pct(v[1],v[0])} for k,v in gd_bins.items()}

# High-confidence errors
hc=[r for r in errors if float(r["confidence"])>=0.60]
hc_bins=defaultdict(int)
for r in hc:
    c=float(r["confidence"]); b="0.60-0.70" if c<.70 else "0.70-0.80" if c<.80 else "0.80-0.90" if c<.90 else ">=0.90"; hc_bins[b]+=1

# Drift first 300 vs last 200
def metrics(sub):
    n=len(sub); e=sum(r["actual_result"]!=r["pred_result"] for r in sub)
    mae=sum(abs(int(r["home_score"])-int(r["away_score"])-float(r["pred_goal_diff"])) for r in sub)/n
    return {"n":n,"accuracy_pct":pct(n-e,n),"errors":e,"goal_diff_mae":round(mae,4),"mean_confidence_pct":round(100*sum(float(r["confidence"]) for r in sub)/n,2)}
drift={"first_300":metrics(rows[:300]),"last_200":metrics(rows[300:])}

# League
lg=defaultdict(list)
for r in rows: lg[r["league"]].append(r)
league={k:metrics(v) for k,v in sorted(lg.items())}

# Most common error transitions
trans=Counter((r["pred_result"],r["actual_result"]) for r in errors)
draw_error_share=pct(sum(r["actual_result"]=="D" or r["pred_result"]=="D" for r in errors),len(errors))
# Also strict definition: errors whose actual result is draw.
actual_draw_error_share=pct(sum(r["actual_result"]=="D" for r in errors),len(errors))

diagnosis={
 "sample_n":500,
 "confusion_matrix":confusion(rows),
 "total_errors":len(errors),
 "error_rate_pct":pct(len(errors),500),
 "error_by_predicted_class":dict(err_by_pred),
 "error_by_actual_class":dict(err_by_actual),
 "draw_error_share_among_errors_involving_draw_pct":draw_error_share,
 "actual_draw_error_share_among_all_errors_pct":actual_draw_error_share,
 "strength_gap_buckets":strength_gap,
 "net_goal_error_concentration":net_goal_error,
 "high_confidence_errors":{"threshold":0.60,"n":len(hc),"share_of_all_errors_pct":pct(len(hc),len(errors)),"bins":dict(hc_bins)},
 "drift":drift,
 "league":league,
 "top_error_transitions":[{"pred":a,"actual":b,"n":n,"share_of_errors_pct":pct(n,len(errors))} for (a,b),n in trans.most_common()],
 "baseline_note":"This is a frozen replay of current V4 code on the exact 500-match sample. It is diagnostic only; no V4 parameter was changed and no V4.1 module was added."
}
json.dump(diagnosis,open(OUTJ,"w",encoding="utf-8"),ensure_ascii=False,indent=2)

lines=["# V4固定500场：8项错误拆解","",f"- 样本：500场；V4代码冻结回放；不改参数。","- 错误数：{len(errors)}；方向错误率：{diagnosis['error_rate_pct']}%。","",
"## 1. H/D/A混淆矩阵","|实际\\预测|H|D|A|","|---|---:|---:|---:|"]
for a in "HDA": lines.append(f"|{a}|{diagnosis['confusion_matrix'][a]['H']}|{diagnosis['confusion_matrix'][a]['D']}|{diagnosis['confusion_matrix'][a]['A']}|")
lines += ["","## 2. 哪一类错误最多",f"- 按预测类别：{err_by_pred.most_common()}","- 按实际类别：{err_by_actual.most_common()}","",
"## 3. 平局错误占比",f"- 实际赛果为平局的错误：{actual_draw_error_share}%","- 错误中涉及平局（预测平/实际平任一）：{draw_error_share}%","",
"## 4. 实力差错误","|区间|样本|错误|错误率|","|---|---:|---:|---:|"]
for k,v in strength_gap.items(): lines.append(f"|{k}|{v['n']}|{v['errors']}|{v['error_rate_pct']}%|")
lines += ["","## 5. 净胜球误差","|误差区间|样本|错误|错误率|","|---|---:|---:|---:|"]
for k,v in net_goal_error.items(): lines.append(f"|{k}|{v['n']}|{v['errors']}|{v['error_rate_pct']}%|")
lines += ["","## 6. 高置信度错误",f"- ≥60%：{len(hc)}场，占全部方向错误 {pct(len(hc),len(errors))}%","- 分档："+str(dict(hc_bins)),"",
"## 7. 时间漂移","|区间|场次|准确率|错误|净胜球MAE|平均置信度|","|---|---:|---:|---:|---:|---:|"]
for k,v in drift.items(): lines.append(f"|{k}|{v['n']}|{v['accuracy_pct']}%|{v['errors']}|{v['goal_diff_mae']}|{v['mean_confidence_pct']}%|")
lines += ["","## 8. 联赛结构","|联赛|场次|准确率|错误|净胜球MAE|平均置信度|","|---|---:|---:|---:|---:|---:|"]
for k,v in league.items(): lines.append(f"|{k}|{v['n']}|{v['accuracy_pct']}%|{v['errors']}|{v['goal_diff_mae']}|{v['mean_confidence_pct']}%|")
lines += ["","## 最大误差来源判定","- 本文件只做诊断，不自动决定V4.1。","- 下一步应根据上述结构选择**一个**模块，并用同一500场与最后200场时序留出集复验。"]
open(OUTM,"w",encoding="utf-8").write("\n".join(lines)+"\n")
print(json.dumps({"rows":500,"errors":len(errors),"output_json":OUTJ,"output_md":OUTM},ensure_ascii=False))
