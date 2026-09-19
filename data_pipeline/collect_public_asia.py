import hashlib, sqlite3
from io import StringIO
from datetime import datetime
import pandas as pd
import requests

DB = "football_model_database.sqlite"
START, END = "2026-01-01", "2026-09-20"

PUBLIC_LEAGUES = {
    "KLEAGUE1": (6, "https://fbref.com/en/comps/55/schedule/K-League-1-Scores-and-Fixtures"),
    "J1": (7, "https://fbref.com/en/comps/25/schedule/J1-League-Scores-and-Fixtures"),
}

def mid(code, date, home, away):
    return hashlib.sha1(f"{code}|{date}|{home}|{away}".encode()).hexdigest()[:20]

def find_schedule_table(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": "football-model-data-loader/1.0"})
    r.raise_for_status()
    tables = pd.read_html(StringIO(r.text))
    for t in tables:
        cols = {str(c).strip() for c in t.columns}
        if {"Date", "Home", "Score", "Away"}.issubset(cols):
            return t
    raise RuntimeError("Schedule table not found")

def season_id(conn, cid):
    row = conn.execute(
        "SELECT season_id FROM seasons WHERE competition_id=? AND season_label=?",
        (cid, "2026")
    ).fetchone()
    if not row:
        raise RuntimeError(f"2026 season not found for competition {cid}")
    return row[0]

def ingest(conn, code, cid, url):
    t = find_schedule_table(url)
    sid = season_id(conn, cid)
    n = 0
    for _, r in t.iterrows():
        date = str(r.get("Date", "")).strip()
        home = str(r.get("Home", "")).strip()
        away = str(r.get("Away", "")).strip()
        score = str(r.get("Score", "")).strip()
        if not date or date == "nan" or not home or not away:
            continue
        try:
            d = pd.to_datetime(date).strftime("%Y-%m-%d")
        except Exception:
            continue
        if not (START <= d <= END):
            continue
        if home in ("Home", "nan") or away in ("Away", "nan"):
            continue
        match_id = mid(code, d, home, away)
        completed = "-" in score and score not in ("-", "")
        ft_h = ft_a = None
        if completed:
            try:
                a, b = score.split("-", 1)
                ft_h, ft_a = int(a.strip()), int(b.strip())
            except Exception:
                completed = False
        conn.execute(
            """INSERT OR IGNORE INTO matches
            (match_id, competition_id, season_id, kickoff, home_team, away_team,
             status, source_status, primary_source_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (match_id, cid, sid, d, home, away,
             "finished" if completed else "scheduled",
             "verified_external", 5)
        )
        if completed:
            result = "H" if ft_h > ft_a else ("A" if ft_h < ft_a else "D")
            conn.execute(
                """INSERT OR REPLACE INTO results
                (match_id, ht_home, ht_away, ft_home, ft_away, result_1x2,
                 completed_at, source_status)
                VALUES (?,?,?,?,?,?,?,?)""",
                (match_id, None, None, ft_h, ft_a, result, d, "verified_external")
            )
        n += 1
    conn.commit()
    return n

def main():
    conn = sqlite3.connect(DB)
    for code, (cid, url) in PUBLIC_LEAGUES.items():
        try:
            print(code, ingest(conn, code, cid, url), "OK")
        except Exception as e:
            print(code, "ERROR:", e)
    conn.close()

if __name__ == "__main__":
    main()
