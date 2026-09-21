import sqlite3, hashlib, requests, io, csv
from datetime import datetime

DB = "football_model_database.sqlite"
START = "2022-01-01"
END = "2026-09-20"

LEAGUES = {
    "EPL": ("E0", 1),
    "LALIGA": ("SP1", 2),
    "BUNDESLIGA": ("D1", 3),
    "SERIEA": ("I1", 4),
    "LIGUE1": ("F1", 5),
}
SEASONS = {"2021/22": "2122", "2022/23": "2223", "2023/24": "2324", "2024/25": "2425", "2025/26": "2526", "2026/27": "2627"}

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

def first_value(row, names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None

def ingest_file(conn, league, code, competition_id, season_label, season_code):
    rows = csv.DictReader(io.StringIO(get_csv(code, season_code)))
    sid = season_id(conn, competition_id, season_label)
    n = 0
    ah_rows = 0
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
            try:
                # Football-Data renamed AH fields across releases. Prefer the
                # regular market line and average AH prices; use closing fields
                # only as a fallback. This is evaluation context, not a model
                # prediction input.
                handicap = first_value(r, ["AHh", "BbAHh", "AHCh"])
                home_odds = first_value(r, ["BbAvAHH", "AvgAHH", "AvgCAHH"])
                away_odds = first_value(r, ["BbAvAHA", "AvgAHA", "AvgCAHA"])
                if handicap not in (None, "") and home_odds not in (None, "") and away_odds not in (None, ""):
                    # Keep the average/regular line as the primary Football-Data observation.
                    # Do not delete Sporttery/SofaScore observations: preserving independent
                    # source lines lets the handicap backtest expand its integer-line coverage.
                    conn.execute(
                        "DELETE FROM sporttery_market WHERE match_id=? AND pool_code='asian_handicap_avg'",
                        (mid,)
                    )
                    conn.execute(
                        """INSERT INTO sporttery_market
                        (match_id,pool_code,handicap,home_value,draw_value,away_value,
                         captured_at,source_status)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (mid, "asian_handicap_avg", float(handicap), float(home_odds),
                         None, float(away_odds), iso_date, "football_data_secondary")
                    )
                    ah_rows += 1

                # Football-Data exposes alternative AH line snapshots in some seasons.
                # Store integer/half/quarter lines as separate source observations; the
                # 3-way integer backtest will keep only integer lines and deduplicate by
                # (match,line), so these extra observations only increase coverage when
                # they genuinely introduce a different line.
                for field, pool in (("AHh", "football_data_ah_open"),
                                    ("BbAHh", "football_data_ah_bookmaker"),
                                    ("AHCh", "football_data_ah_close")):
                    value = r.get(field)
                    if value in (None, ""):
                        continue
                    try:
                        v = float(value)
                    except (TypeError, ValueError):
                        continue
                    if handicap not in (None, "") and abs(v - float(handicap)) < 1e-9:
                        continue
                    if home_odds in (None, "") or away_odds in (None, ""):
                        continue
                    conn.execute(
                        """INSERT INTO sporttery_market
                        (match_id,pool_code,handicap,home_value,draw_value,away_value,
                         captured_at,source_status)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (mid, pool, v, float(home_odds), None, float(away_odds),
                         iso_date, "football_data_secondary_alt_line")
                    )
                    ah_rows += 1
            except (TypeError, ValueError):
                pass
        n += 1
    conn.commit()
    print(league, season_label, "asian_handicap_rows", ah_rows)
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
