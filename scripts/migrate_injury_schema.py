import sqlite3
from pathlib import Path

DB=Path("football_model_database.sqlite")

def add_column(con, table, col, typ):
    cols={r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

def main():
    con=sqlite3.connect(DB)
    # Versioned, idempotent migration for T-12h player availability data.
    for col,typ in [
        ("position","TEXT"),("availability_prob","REAL"),("expected_start_prob","REAL"),
        ("impact_attack","REAL"),("impact_defense","REAL"),("observed_at","TEXT"),
        ("data_cutoff","TEXT"),("source_url","TEXT")]:
        add_column(con,"injuries",col,typ)
    con.execute("""CREATE TABLE IF NOT EXISTS lineup_projection (
      match_id TEXT NOT NULL,
      team_side TEXT NOT NULL,
      expected_start_strength REAL NOT NULL DEFAULT 0.0,
      attack_delta REAL NOT NULL DEFAULT 0.0,
      defense_delta REAL NOT NULL DEFAULT 0.0,
      uncertainty REAL NOT NULL DEFAULT 0.0,
      player_count INTEGER NOT NULL DEFAULT 0,
      data_cutoff TEXT NOT NULL,
      source_status TEXT NOT NULL DEFAULT 'derived',
      PRIMARY KEY(match_id,team_side)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      applied_at TEXT NOT NULL
    )""")
    con.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(2,datetime('now'))")
    con.commit(); con.close()
    print("migration v2 applied")

if __name__=="__main__": main()
