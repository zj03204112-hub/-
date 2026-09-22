import json, sqlite3
from collections import Counter

DB='football_model_database.sqlite'
OUT='data/handicap_sample_bridge_audit.json'
TARGET=(-3,-2,-1,1,2,3)
SOURCES=('asian_handicap_avg','sofascore_asian_featured','sgodds_open','football_data_ah_close','football_data_ah_bookmaker','football_data_ah_open')

def line(x):
    try:
        v=float(x)
        return int(round(v)) if abs(v-round(v))<1e-9 else None
    except Exception:
        return None

def main():
    con=sqlite3.connect(DB)
    total=con.execute("""SELECT COUNT(*) FROM matches m JOIN results r ON r.match_id=m.match_id JOIN competitions c ON c.competition_id=m.competition_id WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL AND c.competition_code IN ('EPL','LALIGA','BUNDESLIGA','SERIEA','LIGUE1','KLEAGUE1','J1')""").fetchone()[0]
    rows=con.execute("""SELECT sm.match_id,sm.handicap,sm.pool_code FROM sporttery_market sm JOIN matches m ON m.match_id=sm.match_id JOIN results r ON r.match_id=m.match_id JOIN competitions c ON c.competition_id=m.competition_id WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL AND sm.handicap IS NOT NULL AND sm.pool_code IN (?,?,?,?,?,?) AND c.competition_code IN ('EPL','LALIGA','BUNDESLIGA','SERIEA','LIGUE1','KLEAGUE1','J1')""",SOURCES).fetchall()
    chosen={}
    for mid,h,pool in rows:
        l=line(h)
        if l not in TARGET: continue
        # Preserve every match/line pair; source is only provenance.
        chosen.setdefault((mid,l),set()).add(pool)
    by_line=Counter(l for _,l in chosen)
    by_source=Counter()
    for pools in chosen.values():
        for p in pools: by_source[p]+=1
    matches_with_line=len(set(mid for mid,_ in chosen))
    out={
      'finished_league_matches':total,
      'finished_matches_with_target_handicap':matches_with_line,
      'coverage':round(matches_with_line/total,4) if total else None,
      'unique_match_line_rows':len(chosen),
      'line_counts':{str(k):by_line[k] for k in TARGET},
      'source_match_line_coverage':dict(by_source),
      'note':'A match without a recorded historical handicap cannot be converted into an actual handicap outcome without inventing a line. Such matches are retained in the ordinary-result universe but excluded from handicap accuracy.'
    }
    with open(OUT,'w',encoding='utf-8') as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps(out,ensure_ascii=False))
    con.close()
if __name__=='__main__': main()
