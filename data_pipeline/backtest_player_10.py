import json, sqlite3, math, sys, os
from datetime import datetime, timedelta

sys.path.insert(0,"data_pipeline")
from compare_models import model_lambdas, matrix

DB="football_model_database.sqlite"
OUT=os.environ.get("BACKTEST_OUT","data/player_backtest_10.json")
TEST_N=int(os.environ.get("BACKTEST_N","10"))
N=8

def outcome(m):
    fh,fa=m[4],m[5]
    return "H" if fh>fa else "D" if fh==fa else "A"

def probs(lh,la):
    mat=matrix(lh,la,True)
    return {"H":sum(mat[i][j] for i in range(N) for j in range(N) if i>j),
            "D":sum(mat[i][j] for i in range(N) for j in range(N) if i==j),
            "A":sum(mat[i][j] for i in range(N) for j in range(N) if i<j)}

def asian_settlement(diff,line):
    """Return Asian handicap settlement probabilities as five states.
    H_full/H_half/D/A_half/A_full. Quarter lines split into adjacent
    whole/half lines; half-win is classified to the corresponding side
    for the user's 3-way handicap pick, while D is reserved for a true push.
    """
    q=round(line*4)/4
    if abs(q-round(q))<1e-9:
        adj=diff+q
        return "H_full" if adj>0 else "D" if abs(adj)<1e-9 else "A_full"
    if abs(abs(q*2)-round(abs(q*2)))<1e-9:
        adj=diff+q
        return "H_full" if adj>0 else "A_full"
    lo=math.floor(q*2)/2
    hi=math.ceil(q*2)/2
    s1=asian_settlement(diff,lo)
    s2=asian_settlement(diff,hi)
    if s1.startswith("H") and s2.startswith("H"): return "H_full"
    if s1.startswith("A") and s2.startswith("A"): return "A_full"
    if s1=="D" and s2=="D": return "D"
    if s1.startswith("H") or s2.startswith("H"): return "H_half"
    if s1.startswith("A") or s2.startswith("A"): return "A_half"
    return "D"

def handicap_probs(lh,la,line):
    mat=matrix(lh,la,True)
    p={"H":0.0,"D":0.0,"A":0.0}
    settle={"H_full":0.0,"H_half":0.0,"D":0.0,"A_half":0.0,"A_full":0.0}
    for i in range(N):
        for j in range(N):
            s=asian_settlement(i-j,line)
            settle[s]+=mat[i][j]
    p["H"]=settle["H_full"]+settle["H_half"]
    p["D"]=settle["D"]
    p["A"]=settle["A_full"]+settle["A_half"]
    return p,settle

def player_form(con,team,cutoff):
    rows=con.execute("""SELECT p.match_id,p.rating,p.minutes_played,m.kickoff
      FROM player_match_stats p JOIN matches m ON m.match_id=p.match_id
      WHERE p.data_source='FotMob public matchDetails'
        AND p.minutes_played>0 AND p.rating IS NOT NULL
        AND m.kickoff < ? AND
        ((p.team_side='home' AND m.home_team=?) OR
         (p.team_side='away' AND m.away_team=?))
      ORDER BY m.kickoff DESC LIMIT 100""",(cutoff,team,team)).fetchall()
    if not rows: return None,0
    # Aggregate the most recent five team matches represented by player rows.
    mids=[]
    for mid,_,_,_ in rows:
        if mid not in mids: mids.append(mid)
        if len(mids)>=5: break
    rr=[r for r in rows if r[0] in mids]
    sw=sum(max(1,float(r[2] or 0)) for r in rr)
    return (sum(float(r[1])*max(1,float(r[2] or 0)) for r in rr)/sw if sw else None),len(mids)

def player_enhanced(con,match):
    mid,kickoff,home,away,fh,fa=match
    ko=datetime.fromisoformat(kickoff[:19])
    cutoff=(ko-timedelta(hours=12)).isoformat(timespec="seconds")
    lh,la=model_lambdas(con,"v3",match,use_lineup=False)
    hf,hn=player_form(con,home,cutoff)
    af,an=player_form(con,away,cutoff)
    if hf is None or af is None:
        return lh,la,hf,af,hn,an
    # Fixed, conservative rating-to-goal coefficient; no fitting on target matches.
    delta=max(-0.08,min(0.08,(hf-af)*0.018))
    lh*=math.exp(delta)
    la*=math.exp(-delta)
    return lh,la,hf,af,hn,an

def main():
    con=sqlite3.connect(DB)
    matches=con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      JOIN provider_event_map pem ON pem.match_id=m.match_id AND pem.provider='fotmob'
      WHERE m.status='finished' AND r.ft_home IS NOT NULL
      ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?""",(max(TEST_N*5,50),)).fetchall()
    eligible=[]
    for m in matches:
        line=con.execute("""SELECT handicap FROM sporttery_market
          WHERE match_id=? AND handicap IS NOT NULL
          ORDER BY captured_at ASC LIMIT 1""",(m[0],)).fetchone()
        if line: eligible.append((m,float(line[0])))
        if len(eligible)>=TEST_N: break
    if len(eligible)<TEST_N:
        raise SystemExit(f"PLAYER_BACKTEST_NEEDS_{TEST_N}:{len(eligible)}")
    rows=[]; base_correct=player_correct=0; base_handicap_correct=player_handicap_correct=0
    for m,line in eligible:
        b_lh,b_la=model_lambdas(con,"v3",m,use_lineup=False)
        bp=probs(b_lh,b_la); bhp,bhs=handicap_probs(b_lh,b_la,line); actual=outcome(m)
        plh,pla,hf,af,hn,an=player_enhanced(con,m)
        pp=probs(plh,pla); php,phs=handicap_probs(plh,pla,line)
        bc=int(max(bp,key=bp.get)==actual); pc=int(max(pp,key=pp.get)==actual)
        ah_settle=asian_settlement(m[4]-m[5],line)
        ah="H" if ah_settle.startswith("H") else "A" if ah_settle.startswith("A") else "D"
        bh_pick=max(bhp,key=bhp.get); ph_pick=max(php,key=php.get)
        bhc=int(bh_pick==ah); phc=int(ph_pick==ah)
        base_correct+=bc; player_correct+=pc; base_handicap_correct+=bhc; player_handicap_correct+=phc
        rows.append({"match_id":m[0],"kickoff":m[1],"home":m[2],"away":m[3],
          "handicap":line,"actual_score":f"{m[4]}-{m[5]}","actual_1x2":actual,
          "handicap_actual":ah,
          "baseline_probs":{k:round(v,4) for k,v in bp.items()},
          "player_probs":{k:round(v,4) for k,v in pp.items()},
          "baseline_pick":max(bp,key=bp.get),"player_pick":max(pp,key=pp.get),
          "handicap_baseline_probs":{k:round(v,4) for k,v in bhp.items()},
          "handicap_player_probs":{k:round(v,4) for k,v in php.items()},
          "baseline_asian_settlement_probs":{k:round(v,4) for k,v in bhs.items()},
          "player_asian_settlement_probs":{k:round(v,4) for k,v in phs.items()},
          "handicap_baseline_pick":bh_pick,"handicap_player_pick":ph_pick,
          "baseline_handicap_hit":bool(bhc),"player_handicap_hit":bool(phc),
          "baseline_handicap_confidence":round(max(bhp.values()),4),
          "player_handicap_confidence":round(max(php.values()),4),
          "baseline_handicap_cover_risk":round(1-max(bhp.values()),4),
          "player_handicap_cover_risk":round(1-max(php.values()),4),
          "baseline_hit":bool(bc),"player_hit":bool(pc),
          "home_player_form_rating":round(hf,4) if hf is not None else None,
          "away_player_form_rating":round(af,4) if af is not None else None,
          "home_prior_matches":hn,"away_prior_matches":an})
    result={"definition":"10-match exploratory player-layer backtest. Target-match player data are never used; player form uses only prior completed FotMob matches before T-12h. Fixed coefficient is not fitted on this sample. Handicap hit rate is evaluated using Asian whole/half/quarter-line settlement semantics; D represents a true push; half-win/half-loss remain with the corresponding side.",
      "n":TEST_N,"baseline_v3_accuracy":round(base_correct/TEST_N,4),
      "player_layer_accuracy":round(player_correct/TEST_N,4),
      "baseline_correct":base_correct,"player_layer_correct":player_correct,
      "baseline_handicap_accuracy":round(base_handicap_correct/TEST_N,4),
      "player_layer_handicap_accuracy":round(player_handicap_correct/TEST_N,4),
      "baseline_handicap_correct":base_handicap_correct,
      "player_layer_handicap_correct":player_handicap_correct,
      "matches":list(reversed(rows))}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
