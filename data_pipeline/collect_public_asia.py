import hashlib, sqlite3
from io import StringIO
from datetime import datetime
import pandas as pd
import requests

DB = "football_model_database.sqlite"
START, END = "2026-01-01", "2026-09-20"

PUBLIC_LEAGUES = {
    "KLEAGUE1": (6, "https://footystats.org/south-korea/k-league-1/fixtures"),
    "J1": (7, "https://data.j-league.or.jp/SFMS01/search?competition_years=2026&competition_frame_ids=1&tv_relay_station_name="),
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
    sid = season_id(conn, cid)
    if code == "J1":
        r = requests.get(url, timeout=30, headers={"User-Agent": "football-model-data-loader/1.0"})
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        if not tables:
            raise RuntimeError("J.League Data Site returned no tables")
        t = tables[0].copy()
        if len(t.columns) < 8:
            raise RuntimeError(f"Unexpected J.League table shape: {t.shape}")
        t = t.iloc[:, :11].copy()
        t.columns = ["Season","Competition","Section","Date","Time","HomeTeam","Score","AwayTeam","Stadium","Attendance","TV"][:len(t.columns)]
        rows = t.rename(columns={"HomeTeam":"Home","AwayTeam":"Away"})
    elif code == "KLEAGUE1":
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 football-model-data-loader/1.0"})
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        candidates = []
        for t in tables:
            cols = {str(c).strip().lower() for c in t.columns}
            if any("home" in c for c in cols) and any("away" in c for c in cols):
                candidates.append(t)
        if not candidates:
            raise RuntimeError("FootyStats K League fixtures table not found")
        rows = candidates[0].copy()
        rename = {}
        for c in rows.columns:
            lc = str(c).lower()
            if "home" in lc: rename[c] = "Home"
            elif "away" in lc: rename[c] = "Away"
            elif "date" in lc: rename[c] = "Date"
            elif "score" in lc or "result" in lc: rename[c] = "Score"
            elif "time" in lc: rename[c] = "Time"
        rows = rows.rename(columns=rename)
    else:
        raise RuntimeError("Unknown Asia league source")
    if not {"Date","Home","Score","Away"}.issubset(rows.columns):
        raise RuntimeError(f"{code} source columns: {list(rows.columns)}")
    n = 0
    import re
    for _, r in rows.iterrows():
        date = str(r.get("Date", "")).strip()
        home = str(r.get("Home", "")).strip()
        away = str(r.get("Away", "")).strip()
        score = str(r.get("Score", "")).strip()
        time = str(r.get("Time", "")).strip()
        if not date or date == "nan" or not home or not away:
            continue
        try:
            d = pd.to_datetime(date).strftime("%Y-%m-%d")
        except Exception:
            continue
        if not (START <= d <= END):
            continue
        match_id = mid(code, d, home, away)
        m = re.search(r"(\d+)\s*[-–:]\s*(\d+)", score)
        completed = bool(m)
        ft_h = int(m.group(1)) if m else None
        ft_a = int(m.group(2)) if m else None
        kickoff = d + ("T" + time if time and time != "nan" else "")
        conn.execute(
            """INSERT OR IGNORE INTO matches
            (match_id, competition_id, season_id, kickoff, home_team, away_team,
             status, source_status, primary_source_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (match_id, cid, sid, kickoff, home, away,
             "finished" if completed else "scheduled", "verified_external", 5)
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
