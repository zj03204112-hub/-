import sqlite3, hashlib, requests, io, csv
from datetime import datetime

DB = "football_model_database.sqlite"
START = "2026-01-01"
END = "2026-09-20"

LEAGUES = {
    "EPL": ("E0", 1),
    "LALIGA": ("SP1", 2),
    "BUNDESLIGA": ("D1", 3),
    "SERIEA": ("I1", 4),
    "LIGUE1": ("F1", 5),
}
SEASONS = {"2025/26": "2526", "2026/27": "2627"}

def match_id(league, date, home, away):
    return hashlib.sha1(f"{league}|{date}|{home}|{away}".encode()).hexdigest()[:20]

def get_csv(code, season):
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
    r = requests.get(url, timeout=30, headers={"User-Agent":"football-model-data-loader/1.0"})
    r.raise_for_status()
    return r.content.decode("latin1")

def season_id(conn, competition_id, label):
    return conn.execute(
        "SELECT season_id FROM seasons WHERE competition_id=? AND season_label=?",
        (competition_id, label)
    ).fetchone()[0]

def ingest_file(conn, league, code, competition_id, season_label, season_code):
    rows = csv.DictReader(io.StringIO(get_csv(code, season_code)))
    sid = season_id(conn, competition_id, season_label)
    n = 0
    for r in rows:
        date = (r.get("Date") or "").strip()
        home = (r.get("HomeTeam") or "").strip()
        away = (r.get("AwayTeam") or "").strip()
        if not date or not home or not away:
            continue
        iso_date = None
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
            try:
                iso_date = datetime.strptime(date, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass
        if iso_date is None or not (START <= iso_date <= END):
            continue

        mid = match_id(league, iso_date, home, away)
        ft_h, ft_a = r.get("FTHG"), r.get("FTAG")
        ht_h, ht_a = r.get("HTHG"), r.get("HTAG")
        completed = ft_h not in (None, "") and ft_a not in (None, "")

        conn.execute(
            """INSERT OR IGNORE INTO matches
            (match_id, competition_id, season_id, kickoff, home_team, away_team,
             status, source_status, primary_source_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (mid, competition_id, sid, iso_date + ("T" + (r.get("Time") or "").strip() if (r.get("Time") or "").strip() else ""), home, away,
             "finished" if completed else "scheduled", "verified_external", 2)
        )
        if completed:
            conn.execute(
                """INSERT OR REPLACE INTO results
                (match_id, ht_home, ht_away, ft_home, ft_away, result_1x2,
                 completed_at, source_status)
                VALUES (?,?,?,?,?,?,?,?)""",
                (mid, int(ht_h) if ht_h else None, int(ht_a) if ht_a else None,
                 int(ft_h), int(ft_a), r.get("FTR"), iso_date, "verified_external")
            )
        n += 1
    conn.commit()
    return n

def main():
    conn = sqlite3.connect(DB)
    total = 0
    for league, (code, cid) in LEAGUES.items():
        for label, scode in SEASONS.items():
            try:
                n = ingest_file(conn, league, code, cid, label, scode)
                print(league, label, n, "OK")
                total += n
            except Exception as e:
                print(league, label, 0, f"ERROR: {e}")
    print("Imported:", total)
    conn.close()

if __name__ == "__main__":
    main()
