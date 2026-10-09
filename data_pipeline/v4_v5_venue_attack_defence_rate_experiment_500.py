#!/usr/bin/env python3
"""Single-module V4 venue-specific attack/defence-rate experiment on frozen 500."""
import csv,json,math,sqlite3
from datetime import datetime,timedelta
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"data_pipeline"))
import compare_models as cm
import v4_v5_joint_engine_validation_500 as base
OUT_JSON=ROOT/"model_validation/v4_v5_venue_attack_defence_rate_experiment_500.json"
OUT_CSV=ROOT/"model_validation/v4_v5_venue_attack_defence_rate_experiment_500.csv"
W=0.65

def venue_dynamic_rates(con,team,venue,cutoff):
    """Same V4 weighting/opponent correction, but only matches at the team's upcoming venue."""
    if venue=="home":
        rows=con.execute("""SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
          FROM matches m JOIN results r ON r.match_id=m.match_id
          WHERE m.kickoff < ? AND m.home_team=? AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
          ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?""",(cutoff,team,cm.HIST_LIMIT)).fetchall()
        h=[((float(r[3]),float(r[4])),r[2]) for r in rows]
    else:
        rows=con.execute("""SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
          FROM matches m JOIN results r ON r.match_id=m.match_id
          WHERE m.kickoff < ? AND m.away_team=? AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
          ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?""",(cutoff,team,cm.HIST_LIMIT)).fetchall()
        h=[((float(r[4]),float(r[3])),r[1]) for r in rows]
    if not h:return 1.15,1.15
    weights=[math.exp(-0.045*i) for i in range(len(h))]
    sw=sum(weights);attack=defence=0.0
    for i,((gf,ga),opp) in enumerate(h):
        ogf,oga=cm.rates(cm.hist(con,opp,cutoff))
        opp_def=max(.75,oga/1.35);opp_att=max(.75,ogf/1.35)
        attack+=(gf/opp_def)*weights[i]
        defence+=(ga/opp_att)*weights[i]
    return max(.2,attack/sw),max(.2,defence/sw)
def v4_venue_rates(con,match):
    mid,kickoff,home,away,fh,fa=match
    cutoff=(datetime.fromisoformat(kickoff[:19])-timedelta(hours=12)).isoformat(timespec="seconds")
    hgf,hga=venue_dynamic_rates(con,home,"home",cutoff)
    agf,aga=venue_dynamic_rates(con,away,"away",cutoff)
    for side,os in (("home",cm.strength(con,home,cutoff)),("away",cm.strength(con,away,cutoff))):
        os=max(.75,min(2.25,os));factor=max(.94,min(1.06,(os/1.5)**.18))
        if side=="home":hgf*=factor;hga/=factor
        else:agf*=factor;aga/=factor
    lh=max(.15,min(4.0,.65+.58*hgf+.30*aga))
    la=max(.12,min(3.5,.58+.58*agf+.30*hga))
    for side in ("home","away"):
        lp=con.execute("SELECT attack_delta,defense_delta FROM lineup_projection WHERE match_id=? AND team_side=?",(mid,side)).fetchone()
        if lp:
            ad=max(-.25,min(.25,float(lp[0] or 0.0)));dd=max(-.25,min(.25,float(lp[1] or 0.0)))
            if side=="home":lh*=math.exp(ad);la*=math.exp(-dd)
            else:la*=math.exp(ad);lh*=math.exp(-dd)
    return lh,la
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
    con=sqlite3.connect(base.DB);cm.HIST_LIMIT=base.HIST_N
    for x in rows:
        vh,va=v4_venue_rates(con,x["match"])
        x["original"]=blend(x["v4"],x["v5"])
        x["experiment"]=blend([vh,va],x["v5"])
        x["v4_venue_rates"]=[vh,va]
    con.close()
    train,cal,test=rows[:300],rows[300:400],rows[400:]
    results={};details=[]
    for label,subset in [("train_300",train),("calibration_100",cal),("final_holdout_100",test)]:
        for arm in ("original","experiment"):
            met,det=metrics(subset,arm,label);results.setdefault(label,{})[arm]=met;details+=det
    b=results["final_holdout_100"]["original"];e=results["final_holdout_100"]["experiment"]
    accepted=bool(e["accuracy"]>b["accuracy"] and e["draw_recall"]>=b["draw_recall"] and e["brier"]<=b["brier"] and e["logloss"]<=b["logloss"])
    out={"status":"COMPLETED_EXPERIMENT_ONLY","sample_n":len(rows),"split":{"train":len(train),"calibration":len(cal),"final_holdout":len(test)},
      "module_changed":"V4 attack/defence-rate generation only: estimate the home side from its pre-match home-only history and away side from away-only history; retain V4's existing opponent adjustment, strength adjustment, fixed baselines, lineup deltas, V5 engine, blend weight and score matrix.",
      "diagnosis":"V4 dynamic_rates currently mixes home and away matches for both teams. This experiment isolates venue-specific attack/conceded rates to test whether venue mixing explains the opposite-direction lambda errors.",
      "selection":"No parameter tuning. Chronological 300/100/100; final 100 used only for acceptance.",
      "results":results,"final_holdout_deltas_experiment_minus_original":{"accuracy":e["accuracy"]-b["accuracy"],"draw_recall":e["draw_recall"]-b["draw_recall"],
        "brier":e["brier"]-b["brier"],"logloss":e["logloss"]-b["logloss"],"score_nll":e["score_nll"]-b["score_nll"]},
      "acceptance_gate":{"accuracy_must_strictly_improve":True,"draw_recall_must_not_decline":True,"brier_must_not_worsen":True,"logloss_must_not_worsen":True},
      "decision":"ACCEPT_EXPERIMENT_ONLY" if accepted else "REJECT_AND_ROLLBACK","production_status":"UNCHANGED; EXPERIMENT_ONLY"}
    OUT_JSON.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as f:
        wr=csv.DictWriter(f,fieldnames=list(details[0].keys()));wr.writeheader();wr.writerows(details)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
