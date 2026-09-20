import sqlite3
from datetime import datetime

DB="football_model_database.sqlite"
START="2026-01-01"
END=datetime.now().date().isoformat()

CHILD_TABLES=[
    "results","sporttery_market","team_features","schedule_intent","injuries",
    "prediction_snapshot","lineup_projection","provider_event_map","player_match_stats",
    "player_features","review"
]

def main():
    con=sqlite3.connect(DB)
    ids=[r[0] for r in con.execute(
        "SELECT match_id FROM matches WHERE substr(kickoff,1,10) < ? OR substr(kickoff,1,10) > ?",
        (START,END)
    )]
    if ids:
        q=",".join("?" for _ in ids)
        for table in CHILD_TABLES:
            con.execute(f"DELETE FROM {table} WHERE match_id IN ({q})", ids)
        con.execute(f"DELETE FROM matches WHERE match_id IN ({q})", ids)
    con.commit()
    remaining=con.execute(
        "SELECT COUNT(*) FROM matches WHERE substr(kickoff,1,10) < ? OR substr(kickoff,1,10) > ?",
        (START,END)
    ).fetchone()[0]
    con.close()
    print({"scope_start":START,"scope_end":END,"removed_matches":len(ids),"remaining_out_of_scope":remaining})

if __name__=="__main__":
    main()
