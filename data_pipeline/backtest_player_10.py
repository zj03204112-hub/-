import json, sqlite3, math, sys
from datetime import datetime, timedelta

sys.path.insert(0,"data_pipeline")
from compare_models import model_lambdas, matrix

DB="football_model_database.sqlite"
OUT="data/player_backtest_10.json"
N=8

def outcome(m):
    fh,fa=m[4],m[5]
    return "H" if fh>fa else "D" if fh==fa else "A"

def probs(lh,la):
    mat=matrix(lh,la,True)
    return {"H":sum(mat[i][j] for i in range(N) for j in range(N) if i>j),
            "D":sum(mat[i][j] for i in range(N) for j in range(N) if i==j),
            "A":sum(mat[i][j] for i in range(N) for j in range(N) if i<j)}

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
      ORDER BY m.kickoff DESC,m.match_id DESC LIMIT 50""").fetchall()
    eligible=[]
    for m in matches:
        line=con.execute("""SELECT handicap FROM sporttery_market
          WHERE match_id=? AND handicap IS NOT NULL
          ORDER BY captured_at ASC LIMIT 1""",(m[0],)).fetchone()
        if line: eligible.append((m,float(line[0])))
        if len(eligible)>=10: break
    if len(eligible)<10:
        raise SystemExit(f"PLAYER_BACKTEST_NEEDS_10:{len(eligible)}")
    rows=[]; base_correct=player_correct=0
    for m,line in eligible:
        b_lh,b_la=model_lambdas(con,"v3",m,use_lineup=False)
        bp=probs(b_lh,b_la); actual=outcome(m)
        plh,pla,hf,af,hn,an=player_enhanced(con,m)
        pp=probs(plh,pla)
        bc=int(max(bp,key=bp.get)==actual); pc=int(max(pp,key=pp.get)==actual)
        base_correct+=bc; player_correct+=pc
        margin=(m[4]-m[5])+line
        ah="H" if margin>0 else "D" if margin==0 else "A"
        rows.append({"match_id":m[0],"kickoff":m[1],"home":m[2],"away":m[3],
          "handicap":line,"actual_score":f"{m[4]}-{m[5]}","actual_1x2":actual,
          "handicap_actual":ah,
          "baseline_probs":{k:round(v,4) for k,v in bp.items()},
          "player_probs":{k:round(v,4) for k,v in pp.items()},
          "baseline_pick":max(bp,key=bp.get),"player_pick":max(pp,key=pp.get),
          "baseline_hit":bool(bc),"player_hit":bool(pc),
          "home_player_form_rating":round(hf,4) if hf is not None else None,
          "away_player_form_rating":round(af,4) if af is not None else None,
          "home_prior_matches":hn,"away_prior_matches":an})
    result={"definition":"10-match exploratory player-layer backtest. Target-match player data are never used; player form uses only prior completed FotMob matches before T-12h. Fixed coefficient is not fitted on this sample.",
      "n":10,"baseline_v3_accuracy":round(base_correct/10,4),
      "player_layer_accuracy":round(player_correct/10,4),
      "baseline_correct":base_correct,"player_layer_correct":player_correct,
      "matches":list(reversed(rows))}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
