import csv, json, math, sqlite3, sys, os
from datetime import datetime, timedelta
sys.path.insert(0,"data_pipeline")
import compare_models as cm

DB="football_model_database.sqlite"
MAP="model_validation/500_match_to_db_match_id_mapping.csv"
OUT="model_validation/v4_frozen_500_full_probability_chain.csv"
SUMMARY="model_validation/v4_frozen_500_full_probability_chain_summary.json"
N=cm.N

def actual(r):
    return "H" if r[4]>r[5] else "D" if r[4]==r[5] else "A"

def prob(m):
    return {"H":sum(m[i][j] for i in range(N) for j in range(N) if i>j),
            "D":sum(m[i][j] for i in range(N) for j in range(N) if i==j),
            "A":sum(m[i][j] for i in range(N) for j in range(N) if i<j)}

def auc(pos,neg,key):
    w=n=0
    for a in pos:
        for b in neg:
            x=key(a); y=key(b); n+=1
            w += 1 if x>y else .5 if x==y else 0
    return w/n if n else float("nan")

def quant(xs):
    xs=sorted(xs)
    def q(p):
        z=(len(xs)-1)*p; lo=int(math.floor(z)); hi=int(math.ceil(z))
        return xs[lo] if lo==hi else xs[lo]+(xs[hi]-xs[lo])*(z-lo)
    return {k:round(q(p),6) for k,p in [("q10",.1),("q25",.25),("q50",.5),("q75",.75),("q90",.9)]}

con=sqlite3.connect(DB)
with open(MAP,encoding="utf-8-sig") as f: mp=list(csv.DictReader(f))
assert len(mp)==500 and len({x["match_id"] for x in mp})==500 and all(x["mapping_status"]=="unique" for x in mp)
db={}
for r in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.status='finished' AND r.ft_home IS NOT NULL"""):
    db[r[0]]=r

rows=[]
for z in mp:
    r=db[z["match_id"]]
    lh,la=cm.model_lambdas(con,"v4",r)
    m_dc=cm.matrix(lh,la,True)
    m_raw=cm.matrix(lh,la,False)
    p_dc=prob(m_dc); p_raw=prob(m_raw)
    ar=actual(r)
    ai,aj=r[4],r[5]
    actual_prob_dc=m_dc[ai][aj] if ai<N and aj<N else 0
    actual_prob_raw=m_raw[ai][aj] if ai<N and aj<N else 0
    low_dc=sum(m_dc[i][j] for i in range(3) for j in range(3))
    low_raw=sum(m_raw[i][j] for i in range(3) for j in range(3))
    rec={"sample_row":int(z["sample_row"]),"match_id":r[0],"kickoff":r[1],"home":r[2],"away":r[3],
         "actual":ar,"home_score":r[4],"away_score":r[5],
         "lambda_home":lh,"lambda_away":la,"lambda_total":lh+la,"lambda_abs_diff":abs(lh-la),
         "raw_p00":m_raw[0][0],"raw_p11":m_raw[1][1],"raw_p22":m_raw[2][2],"raw_p33":m_raw[3][3],
         "raw_low_3x3":low_raw,"raw_pH":p_raw["H"],"raw_pD":p_raw["D"],"raw_pA":p_raw["A"],
         "dc_p00":m_dc[0][0],"dc_p11":m_dc[1][1],"dc_p22":m_dc[2][2],"dc_p33":m_dc[3][3],
         "dc_low_3x3":low_dc,"dc_pH":p_dc["H"],"dc_pD":p_dc["D"],"dc_pA":p_dc["A"],
         "actual_score_prob_raw":actual_prob_raw,"actual_score_prob_dc":actual_prob_dc,
         "dc_minus_raw_pD":p_dc["D"]-p_raw["D"],
         "dc_minus_raw_low_3x3":low_dc-low_raw}
    # Full 8x8 matrices, explicit cells.
    for i in range(N):
        for j in range(N):
            rec[f"raw_{i}-{j}"]=m_raw[i][j]
            rec[f"dc_{i}-{j}"]=m_dc[i][j]
    rows.append(rec)

draw=[r for r in rows if r["actual"]=="D"]; non=[r for r in rows if r["actual"]!="D"]
summary={
 "version_lock":{"compare_models_file":"current checked-in version","RHO":cm.RHO,"N":cm.N,"hist_limit":cm.HIST_LIMIT,
                 "model":"v4","cutoff":"kickoff-12h","matrix":"8x8 normalized Poisson; DC tau when enabled"},
 "n":len(rows),"actual_draws":len(draw),"actual_non_draws":len(non),
 "auc_draw_vs_non_draw":{
   "dc_pD":auc(draw,non,lambda x:x["dc_pD"]),
   "raw_pD":auc(draw,non,lambda x:x["raw_pD"]),
   "dc_p00":auc(draw,non,lambda x:x["dc_p00"]),
   "dc_p11":auc(draw,non,lambda x:x["dc_p11"]),
   "dc_p22":auc(draw,non,lambda x:x["dc_p22"]),
   "dc_p33":auc(draw,non,lambda x:x["dc_p33"]),
   "dc_low_3x3":auc(draw,non,lambda x:x["dc_low_3x3"]),
   "raw_low_3x3":auc(draw,non,lambda x:x["raw_low_3x3"]),
   "lambda_total":auc(draw,non,lambda x:x["lambda_total"]),
   "lambda_abs_diff_inverse":auc(draw,non,lambda x:-x["lambda_abs_diff"]),
   "actual_score_prob_dc":auc(draw,non,lambda x:x["actual_score_prob_dc"])},
 "quantiles":{"draw":{"dc_pD":quant([x["dc_pD"] for x in draw]),"raw_pD":quant([x["raw_pD"] for x in draw]),
                      "lambda_total":quant([x["lambda_total"] for x in draw]),"lambda_abs_diff":quant([x["lambda_abs_diff"] for x in draw]),
                      "dc_low_3x3":quant([x["dc_low_3x3"] for x in draw])},
             "non_draw":{"dc_pD":quant([x["dc_pD"] for x in non]),"raw_pD":quant([x["raw_pD"] for x in non]),
                      "lambda_total":quant([x["lambda_total"] for x in non]),"lambda_abs_diff":quant([x["lambda_abs_diff"] for x in non]),
                      "dc_low_3x3":quant([x["dc_low_3x3"] for x in non])}},
 "dc_vs_raw":{"mean_pD_delta":sum(x["dc_minus_raw_pD"] for x in rows)/len(rows),
              "mean_low3x3_delta":sum(x["dc_minus_raw_low_3x3"] for x in rows)/len(rows),
              "draw_pD_delta_mean":sum(x["dc_minus_raw_pD"] for x in draw)/len(draw),
              "non_draw_pD_delta_mean":sum(x["dc_minus_raw_pD"] for x in non)/len(non)},
 "lambda_bins":[],
 "top_draw_signals":sorted(draw,key=lambda x:x["dc_pD"],reverse=True)[:20]
}
for lo,hi in [(0,1),(1,1.5),(1.5,2),(2,2.5),(2.5,3),(3,3.5),(3.5,4),(4,5),(5,99)]:
    a=[x for x in rows if lo<=x["lambda_total"]<hi]
    if a: summary["lambda_bins"].append({"lo":lo,"hi":hi,"n":len(a),"draws":sum(x["actual"]=="D" for x in a),
       "draw_rate":sum(x["actual"]=="D" for x in a)/len(a),"mean_pD":sum(x["dc_pD"] for x in a)/len(a)})
summary["lambda_abs_diff_bins"]=[]
for lo,hi in [(0,.1),(.1,.2),(.2,.3),(.3,.5),(.5,.8),(.8,1.2),(1.2,99)]:
    a=[x for x in rows if lo<=x["lambda_abs_diff"]<hi]
    if a: summary["lambda_abs_diff_bins"].append({"lo":lo,"hi":hi,"n":len(a),"draws":sum(x["actual"]=="D" for x in a),
       "draw_rate":sum(x["actual"]=="D" for x in a)/len(a),"mean_pD":sum(x["dc_pD"] for x in a)/len(a)})
with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
with open(SUMMARY,"w",encoding="utf-8") as f: json.dump(summary,f,ensure_ascii=False,indent=2)
print(json.dumps(summary,ensure_ascii=False))
