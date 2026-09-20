import sqlite3
from datetime import datetime

DB="football_model_database.sqlite"

def add_col(con, table, col, ddl):
    cols={r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

def main():
    con=sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS provider_event_map (
        match_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        event_id TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY(match_id,provider)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS player_match_stats (
        match_id TEXT NOT NULL,
        team_side TEXT NOT NULL,
        player_id TEXT NOT NULL,
        player_name TEXT NOT NULL,
        position TEXT,
        starter INTEGER NOT NULL DEFAULT 0,
        minutes_played REAL NOT NULL DEFAULT 0,
        rating REAL,
        goals REAL NOT NULL DEFAULT 0,
        assists REAL NOT NULL DEFAULT 0,
        xg REAL NOT NULL DEFAULT 0,
        xa REAL NOT NULL DEFAULT 0,
        shots REAL NOT NULL DEFAULT 0,
        key_passes REAL NOT NULL DEFAULT 0,
        tackles REAL NOT NULL DEFAULT 0,
        interceptions REAL NOT NULL DEFAULT 0,
        clearances REAL NOT NULL DEFAULT 0,
        data_source TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY(match_id,team_side,player_id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS player_features (
        match_id TEXT NOT NULL,
        team_side TEXT NOT NULL,
        player_id TEXT NOT NULL,
        player_name TEXT NOT NULL,
        start_prob REAL NOT NULL DEFAULT 0,
        appearance_prob REAL NOT NULL DEFAULT 0,
        expected_minutes REAL NOT NULL DEFAULT 0,
        attack90 REAL NOT NULL DEFAULT 0,
        defense90 REAL NOT NULL DEFAULT 0,
        influence90 REAL NOT NULL DEFAULT 0,
        sample_minutes REAL NOT NULL DEFAULT 0,
        data_cutoff TEXT NOT NULL,
        source_status TEXT NOT NULL DEFAULT 'derived',
        PRIMARY KEY(match_id,team_side,player_id)
    )""")
    con.execute("INSERT OR REPLACE INTO schema_migrations(version,applied_at) VALUES(?,?)",(2,datetime.utcnow().isoformat(timespec="seconds")))
    con.commit(); con.close()
    print("player schema migration v2 OK")

if __name__=="__main__":
    main()
