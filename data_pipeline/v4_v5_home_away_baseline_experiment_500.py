#!/usr/bin/env python3
"""Single-module V4 home/away baseline experiment on the frozen 500."""
import csv,json,math,sqlite3
from datetime import datetime,timedelta
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import compare_models as cm
import v4_v5_joint_engine_validation_500 as base
OUT_JSON=ROOT/"model_validation/v4_v5_home_away_baseline_experiment_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_home_away_baseline_experiment_500.csv"
W=0.65

def weighted_mean(vals):
    if not vals:return None
    weights=[math.exp(-0.08*i) for i in range(len(vals))]
    return sum(v*weights[i] for i,v in enumerate(vals))/sum(weights)
def league_baselines(con,cutoff,league_col,league):
    hist=base.load_history(con,cutoff,league_col,league)
    if not hist:return 1.45,1.15
    hg=[float(r[3]) for r in hist];ag=[float(r[4]) for r in hist]
    return max(.45,weighted_mean(hg[:200]) or 1.45),max(.35,weighted_mean(ag[:200]) or 1.15)
def v4_dynamic_baseline(con,match,league_col,league):
    """Identical V4 rate/strength/lineup code; only fixed lambda intercepts become league-calibrated."""
    mid,kickoff,home,away,fh,fa=match
    cutoff=(datetime.fromisoformat(kickoff[:19])-timedelta(hours=12)).isoformat(timespec="seconds")
    hgf,hga=cm.dynamic_rates(con,home,cutoff);agf,aga=cm.dynamic_rates(con,away,cutoff)
    for side,os in (("home",cm.strength(con,home,cutoff)),("away",cm.strength(con,away,cutoff))):
        os=max(.75,min(2.25,os));factor=max(.94,min(1.06,(os/1.5)**.18))
        if side=="home":hgf*=factor;hga/=factor
        else:agf*=factor;aga/=factor
    league_h,league_a=league_baselines(con,cutoff,league_col,league)
    # Center the existing 0.58 attack + 0.30 opponent-defence terms around the league baseline.
    # This changes only the intercept/baseline; rate weights, strength adjustment, and matrix stay fixed.
    mean_goal=(league_h+league_a)/2
    home_intercept=league_h-0.88*mean_goal
    away_intercept=league_a-0.88*mean_goal
    lh=max(.15,min(4.0,home_intercept+.58*hgf+.30*aga))
    la=max(.12,min(3.5,away_intercept+.58*agf+.30*hga))
    for side in ("home","away"):
        lp=con.execute("SELECT attack_delta,defense_delta FROM lineup_projection WHERE match_id=? AND team_side=?",(mid,side)).fetchone()
        if lp:
            ad=max(-.25,min(.25,float(lp[0] or 0.0)));dd=max(-.25,min(.25,float(lp[1] or 0.0)))
            if side=="home":lh*=math.exp(ad);la*=math.exp(-dd)
            else:la*=math.exp(ad);lh*=math.exp(-dd)
    return lh,la,home_intercept,away_intercept
def actual(x):
    h,a=int(x["match"][4]),int(x["match"][5])
    return h,a,"H" if h>a else "D" if h==a else "A"
def blend(v4,v5):
    return [math.exp(W*math.log(max(1e-9,v4[0]))+(1-W)*math.log(max(1e-9,v5[0]))),
            math.exp(W*math.log(max(1e-9,v4[1]))+(1-W)*math.log(max(1e-9,v5[1])))]
def metrics(records,arm,split):
    n=len(records);correct=tp=fp=fn=0;brier=ll=snll=0.0;details=[];groups={}
    for x in records:
        h,a,act=actual(x);lh,la=x[arm];m=base.matrix(lh,la);p=base.matrix_probs(m);pred=max(p,key=p.get)
        correct+=pred==act;tp+=pred=="D" and act=="D";fp+=pred=="D" and act!="D";fn+=pred!="D" and act=="D"
        brier+=base.brier(p,act);ll+=base.logloss(p,act);snll+=base.score_nll(m,(h,a))
        cls=("correct_home_win" if act=="H" and pred=="H" else "incorrect_home_win" if act=="H" else
             "correct_away_win" if act=="A" and pred=="A" else "incorrect_away_win" if act=="A" else
             "correct_draw" if pred=="D" else "missed_draw")
        g=groups.setdefault(cls,{"n":0,"correct":0});g["n"]+=1;g["correct"]+=int(pred==act)
        details.append({"match_id":x["match"][0],"kickoff":x["match"][1],"home":x["match"][2],"away":x["match"][3],
          "actual_score":f"{h}-{a}","actual_90":act,"pred_90":pred,"classification":cls,"split":split,"arm":arm,
          "lambda_home":round(lh,6),"lambda_away":round(la,6),"lambda_total":round(lh+la,6),
          "p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),
          "brier":round(base.brier(p,act),6),"logloss":round(base.logloss(p,act),6),"score_nll":round(base.score_nll(m,(h,a)),6)})
    for g in groups.values():g["accuracy_within_group"]=g["correct"]/g["n"]
    return {"n":n,"correct":correct,"accuracy":correct/n,"draw_tp":tp,"draw_fp":fp,"draw_fn":fn,
      "draw_recall":tp/(tp+fn) if tp+fn else 0,"brier":brier/n,"logloss":ll/n,"score_nll":snll/n,"groups":groups},details
def main():
    rows=base.get_rows();rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    con=sqlite3.connect(base.DB);league_col=base.get_league_col(con);cm.HIST_LIMIT=base.HIST_N
    for x in rows:
        m=x["match"];league=x["mapping"].get("competition_code") or x["mapping"].get("league") or ""
        v4h,v4a,ih,ia=v4_dynamic_baseline(con,m,league_col,league)
        x["original"]=blend(x["v4"],x["v5"])
        x["experiment"]=blend([v4h,v4a],x["v5"])
        x["v4_dynamic_baseline"]=[v4h,v4a];x["home_intercept"]=ih;x["away_intercept"]=ia
    con.close()
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    results={};details=[]
    for label,subset in [("train_300",train),("calibration_100",cal),("final_holdout_100",test)]:
        for arm in ("original","experiment"):
            met,det=metrics(subset,arm,label);results.setdefault(label,{})[arm]=met;details+=det
    b=results["final_holdout_100"]["original"];e=results["final_holdout_100"]["experiment"]
    accepted=bool(e["accuracy"]>b["accuracy"] and e["draw_recall"]>=b["draw_recall"] and e["brier"]<=b["brier"] and e["logloss"]<=b["logloss"])
    out={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":len(rows),"split":{"train":len(train),"calibration":len(cal),"final_holdout":len(test)},
      "module_changed":"V4 home/away lambda baseline intercept only. Replace fixed +0.65 home / +0.58 away intercepts with league-baseline-centered intercepts; keep attack/defence weights 0.58/0.30, strength adjustment, lineup deltas, V5 reconstruction, 0.65 blend weight and score matrix unchanged.",
      "diagnosis":"V4 uses fixed intercepts across leagues despite different league home/away goal baselines. Experiment estimates league baselines from pre-match history only and changes intercepts to make the baseline expected lambda match the league-specific home/away mean.",
      "selection":"No tuning; chronological 300/100/100. Final 100 used only for acceptance.",
      "results":results,"final_holdout_deltas_experiment_minus_original":{"accuracy":e["accuracy"]-b["accuracy"],"draw_recall":e["draw_recall"]-b["draw_recall"],
        "brier":e["brier"]-b["brier"],"logloss":e["logloss"]-b["logloss"],"score_nll":e["score_nll"]-b["score_nll"]},
      "acceptance_gate":{"accuracy_must_strictly_improve":True,"draw_recall_must_not_decline":True,"brier_must_not_worsen":True,"logloss_must_not_worsen":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK","production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    OUT_JSON.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(details[0].keys()));wr.writeheader();wr.writerows(details)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
