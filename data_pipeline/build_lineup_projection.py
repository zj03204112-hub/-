import json, math, sqlite3
from datetime import datetime, timedelta

DB="football_model_database.sqlite"

# Injury/player-impact inputs are intentionally data-driven. The pipeline never
# invents a player rating: impact_attack/impact_defense must come from a source.
def iso_cutoff(kickoff):
    return (datetime.fromisoformat(kickoff[:19])-timedelta(hours=12)).isoformat(timespec="seconds")

def main():
    con=sqlite3.connect(DB)
    rows=con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches WHERE status='finished' ORDER BY kickoff,match_id").fetchall()
    con.execute("DELETE FROM lineup_projection")
    for mid,kickoff,home,away in rows:
        cutoff=iso_cutoff(kickoff)
        for side in ("home","away"):
            data=con.execute("""SELECT availability_prob,expected_start_prob,impact_attack,impact_defense
              FROM injuries WHERE match_id=? AND team_side=?
              AND (data_cutoff IS NULL OR data_cutoff<=?)""",(mid,side,cutoff)).fetchall()
            attack=0.0; defense=0.0; uncertainty=0.0; count=0
            for av,sp,ia,idf in data:
                av=1.0 if av is None else max(0.0,min(1.0,float(av)))
                sp=1.0 if sp is None else max(0.0,min(1.0,float(sp)))
                w=(1.0-av)*sp
                if ia is not None: attack -= float(ia)*w
                if idf is not None: defense += float(idf)*w
                uncertainty += w if (ia is None or idf is None) else 0.0
                count += 1
            # Store log-rate deltas. Clamp to avoid one uncertain player dominating.
            attack=max(-0.25,min(0.25,attack))
            defense=max(-0.25,min(0.25,defense))
            uncertainty=max(0.0,min(1.0,uncertainty/max(1,count))) if count else 0.0
            strength=max(0.0,min(1.0,math.exp(attack)-1.0))
            con.execute("""INSERT INTO lineup_projection
              (match_id,team_side,expected_start_strength,attack_delta,defense_delta,uncertainty,player_count,data_cutoff)
              VALUES (?,?,?,?,?,?,?,?)""",(mid,side,strength,attack,defense,uncertainty,count,cutoff))
    con.commit()
    n=con.execute("SELECT COUNT(*) FROM lineup_projection").fetchone()[0]
    populated=con.execute("SELECT COUNT(*) FROM lineup_projection WHERE player_count>0").fetchone()[0]
    print(json.dumps({"rows":n,"populated":populated,"rule":"T-12h only; player impact must be sourced, no invented ratings"},ensure_ascii=False))
    con.close()

if __name__=="__main__": main()
