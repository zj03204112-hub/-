#!/usr/bin/env python3
"""Audit venue-matched opponent-strength normalization; one-module experiment on frozen 500."""
import csv,json,math,sqlite3
from pathlib import Path
from datetime import datetime,timedelta
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import v4_v5_joint_engine_validation_500 as base
OUT_JSON=ROOT/"model_validation/v4_v5_opponent_venue_normalization_experiment_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_opponent_venue_normalization_experiment_500.csv"
W=0.65
N=base.N

def weighted_mean(vals):
    if not vals:return None
    w=[math.exp(-0.08*i) for i in range(len(vals))]
    return sum(v*w[i] for i,v in enumerate(vals))/sum(w)
def v5_venue_normalized(con,match,league_col,league):
    """Same reconstructed V5 engine; only opponent normalization uses venue-matched opponent rates."""
    mid,kickoff,home,away,fh,fa=match
    cutoff=(datetime.fromisoformat(kickoff[:19])-timedelta(hours=12)).isoformat(timespec="seconds")
    hist=base.load_history(con,cutoff,league_col,league)
    if not hist:return 1.45,1.15,cutoff,0
    home_goals=[float(r[3]) for r in hist]; away_goals=[float(r[4]) for r in hist]
    league_h=max(0.45,weighted_mean(home_goals[:200]) or 1.45)
    league_a=max(0.35,weighted_mean(away_goals[:200]) or 1.15)
    def raw_venue_rates(team,venue):
        gf=[];ga=[]
        for r in hist:
            _,h,a,gh,ga_=r[:5]
            if venue=="home" and h==team: gf.append(float(gh));ga.append(float(ga_))
            elif venue=="away" and a==team: gf.append(float(ga_));ga.append(float(gh))
            if len(gf)>=base.HIST_N:break
        return weighted_mean(gf),weighted_mean(ga),len(gf)
    def venue_rates(team,venue):
        gf=[];ga=[]
        # If target team played at home, opponent was away; and vice versa.
        opponent_venue="away" if venue=="home" else "home"
        for r in hist:
            _,h,a,gh,ga_=r[:5]
            if venue=="home" and h==team: opp=a;xgf,xga=float(gh),float(ga_)
            elif venue=="away" and a==team: opp=h;xgf,xga=float(ga_),float(gh)
            else:continue
            ogf,oga,_=raw_venue_rates(opp,opponent_venue)
            # Correct denominator depends on the opponent's venue:
            # away opponent GF is compared with league away baseline; home opponent GF with league home baseline.
            opp_attack_base=league_a if opponent_venue=="away" else league_h
            # goals conceded by an away team correspond to home-scoring baseline, and vice versa.
            opp_defence_base=league_h if opponent_venue=="away" else league_a
            def_ratio=max(0.65,min(1.50,(oga or opp_defence_base)/opp_defence_base))
            att_ratio=max(0.65,min(1.50,(ogf or opp_attack_base)/opp_attack_base))
            gf.append(xgf/def_ratio)
            ga.append(xga/att_ratio)
            if len(gf)>=base.HIST_N:break
        return weighted_mean(gf),weighted_mean(ga),len(gf)
    hgf,hga,nh=venue_rates(home,"home")
    agf,aga,na=venue_rates(away,"away")
    h_attack=max(0.20,min(2.50,(hgf or league_h)/league_h))
    h_defence=max(0.20,min(2.50,(hga or league_a)/league_a))
    a_attack=max(0.20,min(2.50,(agf or league_a)/league_a))
    a_defence=max(0.20,min(2.50,(aga or league_h)/league_h))
    lh=max(0.15,min(4.0,league_h*h_attack*a_defence))
    la=max(0.12,min(3.5,league_a*a_attack*h_defence))
    return lh,la,cutoff,min(nh,na)
def actual(x):
    h,a=int(x["match"][4]),int(x["match"][5])
    return h,a,"H" if h>a else "D" if h==a else "A"
def matrix(lh,la):return base.matrix(lh,la)
def score(records,arm,split):
    n=len(records);correct=tp=fp=fn=0;brier=ll=snll=0.0
    groups={k:{"n":0,"correct":0,"draw_tp":0,"draw_fn":0} for k in ["correct_home_win","incorrect_home_win","correct_away_win","incorrect_away_win","missed_draw","correct_draw"]}
    details=[]
    for r in records:
        h,a,act=actual(r);lh,la=r[arm];m=matrix(lh,la);p=base.matrix_probs(m);pred=max(p,key=p.get)
        correct+=pred==act;tp+=pred=="D" and act=="D";fp+=pred=="D" and act!="D";fn+=pred!="D" and act=="D"
        brier+=base.brier(p,act);ll+=base.logloss(p,act);snll+=base.score_nll(m,(h,a))
        cls=("correct_home_win" if act=="H" and pred=="H" else "incorrect_home_win" if act=="H" else
             "correct_away_win" if act=="A" and pred=="A" else "incorrect_away_win" if act=="A" else
             "correct_draw" if pred=="D" else "missed_draw")
        g=groups[cls];g["n"]+=1;g["correct"]+=int(pred==act);g["draw_tp"]+=int(pred=="D" and act=="D");g["draw_fn"]+=int(pred!="D" and act=="D")
        details.append({"match_id":r["match"][0],"kickoff":r["match"][1],"home":r["match"][2],"away":r["match"][3],
          "actual_score":f"{h}-{a}","actual_90":act,"split":split,"arm":arm,"classification":cls,
          "lambda_home":round(lh,6),"lambda_away":round(la,6),"lambda_total":round(lh+la,6),
          "lambda_home_error":round(lh-h,6),"lambda_away_error":round(la-a,6),
          "p_home":round(p["H"],6),"p_draw":round(p["D"],6),"p_away":round(p["A"],6),"pred_90":pred,
          "brier":round(base.brier(p,act),6),"logloss":round(base.logloss(p,act),6),"score_nll":round(base.score_nll(m,(h,a)),6)})
    for g in groups.values():
        g["accuracy_within_group"]=g["correct"]/g["n"] if g["n"] else None
    return {"n":n,"accuracy":correct/n,"correct":correct,"draw_tp":tp,"draw_fp":fp,"draw_fn":fn,
      "draw_recall":tp/(tp+fn) if tp+fn else 0,"brier":brier/n,"logloss":ll/n,"score_nll":snll/n,
      "groups":groups},details
def main():
    rows=base.get_rows();rows.sort(key=lambda x:(x["match"][1],x["match"][0]))
    con=sqlite3.connect(base.DB);league_col=base.get_league_col(con)
    for x in rows:
        m=x["match"];league=x["mapping"].get("competition_code") or x["mapping"].get("league") or ""
        lh,la,cutoff,venue_n=v5_venue_normalized(con,m,league_col,league)
        x["v5_fixed"]=[lh,la];x["cutoff"]=cutoff;x["venue_history_n_fixed"]=venue_n
        x["original"]=[math.exp(W*math.log(max(1e-9,x["v4"][0]))+(1-W)*math.log(max(1e-9,x["v5"][0]))),
                       math.exp(W*math.log(max(1e-9,x["v4"][1]))+(1-W)*math.log(max(1e-9,x["v5"][1])))]
        x["experiment"]=[math.exp(W*math.log(max(1e-9,x["v4"][0]))+(1-W)*math.log(max(1e-9,lh))),
                         math.exp(W*math.log(max(1e-9,x["v4"][1]))+(1-W)*math.log(max(1e-9,la)))]
    con.close()
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    summaries={};details=[]
    for label,subset in [("train_300",train),("calibration_100",cal),("final_holdout_100",test)]:
        for arm in ("original","experiment"):
            met,det=score(subset,arm,label);summaries.setdefault(label,{})[arm]=met;details+=det
    b=summaries["final_holdout_100"]["original"];e=summaries["final_holdout_100"]["experiment"]
    accepted=bool(e["accuracy"]>b["accuracy"] and e["draw_recall"]>=b["draw_recall"] and
                  e["brier"]<=b["brier"] and e["logloss"]<=b["logloss"])
    out={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":len(rows),"split":{"train":len(train),"calibration":len(cal),"final_holdout":len(test)},
      "module_changed":"V5 reconstructed engine opponent-strength normalization only: replace mixed-venue opponent overall rates and shared league-home denominator with opponent rates for the matching venue and corresponding home/away league baselines.",
      "unchanged":"V4 lambdas, V5 attack/defence rate construction, venue baselines, final score matrix, classifier, blend weight (V4 share 0.65).",
      "diagnosis":"Existing V5 normalization uses opponent overall GF/GA and divides both by league_home baseline. That mixes venue effects and mismatches away/home goal baselines. The experiment changes only that opponent normalization.",
      "selection":"No parameter tuning. Chronological train 300, calibration 100, final holdout 100; final holdout used only once for acceptance.",
      "results":summaries,"final_holdout_deltas_experiment_minus_original":{"accuracy":e["accuracy"]-b["accuracy"],
       "draw_recall":e["draw_recall"]-b["draw_recall"],"brier":e["brier"]-b["brier"],"logloss":e["logloss"]-b["logloss"],"score_nll":e["score_nll"]-b["score_nll"]},
      "acceptance_gate":{"accuracy_must_strictly_improve":True,"draw_recall_must_not_decline":True,"brier_must_not_worsen":True,"logloss_must_not_worsen":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK","production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    OUT_JSON.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(details[0].keys()));wr.writeheader();wr.writerows(details)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
