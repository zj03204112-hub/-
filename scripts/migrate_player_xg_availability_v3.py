import sqlite3
from datetime import datetime, timezone

DB = "football_model_database.sqlite"

def main():
    con = sqlite3.connect(DB)
    cols = {r[1] for r in con.execute("PRAGMA table_info(player_match_stats)")}
    for col in ("xg_source_available", "xa_source_available"):
        if col not in cols:
            con.execute(f"ALTER TABLE player_match_stats ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    con.execute(
        "INSERT OR REPLACE INTO schema_migrations(version,applied_at) VALUES(?,?)",
        (3, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    )
    con.commit()
    print({
        "migration": 3,
        "added": ["xg_source_available", "xa_source_available"],
        "note": "Existing rows default to unavailable; numeric zero is not treated as observed xG/xA."
    })
    con.close()

if __name__ == "__main__":
    main()
