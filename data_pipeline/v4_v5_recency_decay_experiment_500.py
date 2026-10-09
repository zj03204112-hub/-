#!/usr/bin/env python3
"""Single-module experiment: tune only V4 recency decay on frozen 500."""
import csv, json, math, sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import compare_models as cm
import v4_v5_joint_engine_validation_500 as base
DB=ROOT/"football_model_database.sqlite"
OUT_JSON=ROOT/"model_validation/v4_v5_recency_decay_experiment_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_recency_decay_experiment_500.csv"
W=0.65
# Fixed grid, selected on chronological calibration only; no final-holdout tuning.
DECAYS=[0.02,0.03,0.045,0.06,0.08]

def dynamic_rates_decay(con,team,cutoff,decay):
    h=cm.hist(con,team,cutoff)
    if not h: return 1.15,1.15
    weights=[math.exp(-decay*i) for i in range(len(h))]
    sw=sum(weights); attack=defence=0.0
    for i,((gf,ga),opp) in enumerate(h):
        ogf,oga=cm.rates(cm.hist(con,opp,cutoff))
        opp_def=max(.75,oga/1.35); opp_att=max(.75,ogf/1.35)
        attack+=(gf/opp_def)*weights[i]
        defence+=(ga/opp_att)*weights[i]
    return max(.2,attack/sw),max(.2,defence/sw)

def v4_decay_lambdas(con,match,decay):
    mid,kickoff,home,away,fh,fa=match
    cutoff=(datetime.fromisoformat(kickoff[:19])-timedelta(hours=12)).isoformat(timespec="seconds")
    hgf,hga=dynamic_rates_decay(con,home,cutoff,decay)
    agf,aga=dynamic_rates_decay(con,away,cutoff,decay)
    for side,os in (("home",cm.strength(con,home,cutoff)),("away",cm.strength(con,away,cutoff))):
        os=max(.75,min(2.25,os)); factor=max(.94,min(1.06,(os/1.5)**.18))
        if side=="home": hgf*=factor; hga/=factor
        else: agf*=factor; aga/=factor
    lh=max(.15,min(4.0,.65+.58*hgf+.30*aga))
    la=max(.12,min(3.5,.58+.58*agf+.30*hga))
    for side in ("home","away"):
        lp=con.execute("SELECT attack_delta,defense_delta FROM lineup_projection WHERE match_id=? AND team_side=?",(mid,side)).fetchone()
        if lp:
            ad=max(-.25,min(.25,float(lp[0] or 0.0))); dd=max(-.25,min(.25,float(lp[1] or 0.0)))
            if side=="home": lh*=math.exp(ad); la*=math.exp(-dd)
            else: la*=math.exp(ad); lh*=math.exp(-dd)
    return lh,la

def blend(v4,v5):
    return [math.exp(W*math.log(max(1e-9,v4[0]))+(1-W)*math.log(max(1e-9,v5[0]))),
            math.exp(W*math.log(max(1e-9,v4[1]))+(1-W)*math.log(max(1e-9,v5[1])))]

def evaluate(rows,arm,split):
    n=correct=tp=fp=fn=0; brier=ll=snll=0.0; details=[]
    for x in rows:
        h,a=int(x["match"][4]),int(x["match"][5])
        lh,la=x[arm]; matrix=base.matrix(lh,la); p=base.matrix_probs(matrix)
        actual="H" if h>a else "D" if h==a else "A"
        pred=max(p,key=p.get)
        n+=1; correct+=int(pred==actual)
        tp+=int(pred=="D" and actual=="D"); fp+=int(pred=="D" and actual!="D"); fn+=int(pred!="D" and actual=="D")
        brier+=base.brier(p,actual); ll+=base.logloss(p,actual); snll+=base.score_nll(matrix,(h,a))
        details.append({"match_id":x["match"][0],"kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
          "split":split,"arm":arm,"actual_score":f"{h}-{a}","actual_90":actual,"pred_90":pred,
          "lambda_home":round(lh,6),"lambda_away":round(la,6),"p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6)})
    return {"n":n,"correct":correct,"accuracy":correct/n,"draw_tp":tp,"draw_fp":fp,"draw_fn":fn,
      "draw_recall":tp/(tp+fn) if tp+fn else 0,"brier":brier/n,"logloss":ll/n,"score_nll":snll/n},details

def main():
    rows=base.get_rows(); rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    if len(rows)!=500: raise RuntimeError(f"Expected frozen 500 sample, got {len(rows)}")
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    con=sqlite3.connect(DB); cm.HIST_LIMIT=base.HIST_N
    # Existing production/audit baseline is fixed at decay=0.045.
    for x in rows:
        x["original"]=blend(x["v4"],x["v5"])
    candidate_scores=[]
    candidate_rows={}
    for decay in DECAYS:
        out=[]
        for x in rows:
            v4=v4_decay_lambdas(con,x["match"],decay)
            out.append({"match":x["match"],"v4":v4,"v5":x["v5"],"candidate":blend(v4,x["v5"])})
        met,_=evaluate(out[300:400],"candidate","calibration_100")
        candidate_scores.append({"decay":decay,**met})
        candidate_rows[decay]=out
    con.close()
    # Primary calibration objective is score NLL; tie-break toward current decay.
    selected=min(candidate_scores,key=lambda z:(z["score_nll"],abs(z["decay"]-0.045)))
    chosen=selected["decay"]
    exp_rows=candidate_rows[chosen]
    original_splits={}; exp_splits={}; details=[]
    for label,subset in (("train_300",train),("calibration_100",cal),("final_holdout_100",test)):
        om,od=evaluate(subset,"original",label); original_splits[label]=om; details+=od
        # Use selected candidate outputs aligned to the same frozen chronological rows.
        ex_subset=[{"match":x["match"],"candidate":candidate_rows[chosen][i]["candidate"]} for i,x in enumerate(subset, start=(0 if label=="train_300" else 300 if label=="calibration_100" else 400))]
        em,ed=evaluate(ex_subset,"candidate",label); exp_splits[label]=em; details+=ed
    b=original_splits["final_holdout_100"]; e=exp_splits["final_holdout_100"]
    accepted=bool(e["accuracy"]>b["accuracy"] and e["draw_recall"]>=b["draw_recall"] and e["brier"]<=b["brier"] and e["logloss"]<=b["logloss"])
    result={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":500,"split":{"train":300,"calibration":100,"final_holdout":100},
      "module_changed":"Only V4 dynamic attack/defence recency decay exponent; opponent adjustment, strength adjustment, fixed baselines, lineup deltas, V5 reconstruction, 0.65 blend and score matrix unchanged.",
      "selection":"Fixed decay grid selected by calibration score NLL only; final holdout untouched until acceptance.",
      "candidate_calibration_scores":candidate_scores,"selected_decay":chosen,
      "results":{"original":original_splits,"experiment":exp_splits},
      "final_holdout_deltas_experiment_minus_original":{"accuracy":e["accuracy"]-b["accuracy"],"draw_recall":e["draw_recall"]-b["draw_recall"],
        "brier":e["brier"]-b["brier"],"logloss":e["logloss"]-b["logloss"],"score_nll":e["score_nll"]-b["score_nll"]},
      "acceptance_gate":{"accuracy_must_strictly_improve":True,"draw_recall_must_not_decline":True,"brier_must_not_worsen":True,"logloss_must_not_worsen":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK",
      "production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    OUT_JSON.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(details[0].keys())); writer.writeheader(); writer.writerows(details)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
