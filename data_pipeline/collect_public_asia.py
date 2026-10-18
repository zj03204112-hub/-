import hashlib, sqlite3
from pathlib import Path
from io import StringIO
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests

DB = "football_model_database.sqlite"
START, END = "2026-01-01", datetime.now(ZoneInfo("Asia/Singapore")).date().isoformat()

# K League 1 is ingested only from the dated, provider-ID-mapped CSV above.
# Do not pass its time-only HTML table through the legacy pandas.read_html fallback.
PUBLIC_LEAGUES = {}

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
        local = "data/auto_results/J1_League_2026_27.csv"
        if Path(local).exists():
            rows = pd.read_csv(local)
            rows["Home"] = rows["HomeTeam"]
            rows["Away"] = rows["AwayTeam"]
            rows["Score"] = rows["FTHG"].astype(str) + "-" + rows["FTAG"].astype(str)
        else:
            r = requests.get(url, timeout=30, headers={"User-Agent": "football-model-data-loader/1.0"})
            r.raise_for_status()
            tables = pd.read_html(StringIO(r.text))
            if not tables:
                raise RuntimeError("J.League Data Site returned no tables")
            t = tables[0].copy()
            t = t.iloc[:, :11]
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
             "finished" if completed else "scheduled", "verified_external", 6)
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

def ingest_j1_csv(conn):
    path = Path("data/auto_results/J1_League_2026_27.csv")
    if not path.exists():
        return 0
    rows = pd.read_csv(path)
    sid = season_id(conn, 7)
    n = 0
    import re
    for _, r in rows.iterrows():
        raw_date = str(r.get("Date", "")).strip()
        # J.League official CSV uses dates such as 26/08/07(金).
        mdate = re.search(r"(\d{2})/(\d{2})/(\d{2})", raw_date)
        if mdate:
            yy, mm, dd = map(int, mdate.groups())
            d = f"{2000 + yy:04d}-{mm:02d}-{dd:02d}"
        else:
            parsed = pd.to_datetime(raw_date, errors="coerce")
            if pd.isna(parsed):
                continue
            d = parsed.strftime("%Y-%m-%d")
        if not (START <= d <= END):
            continue
        home = str(r.get("HomeTeam","")).strip()
        away = str(r.get("AwayTeam","")).strip()
        if not home or not away or home == "nan" or away == "nan":
            continue
        import re
        if pd.notna(r.get("FTHG")) and pd.notna(r.get("FTAG")):
            mh, ma = int(float(r.get("FTHG"))), int(float(r.get("FTAG")))
        else:
            score = str(r.get("Score","")).strip()
            m = re.search(r"(\d+)\s*[-–:]\s*(\d+)", score)
            if not m:
                continue
            mh, ma = int(m.group(1)), int(m.group(2))
        midv = mid("J1", d, home, away)
        tm = str(r.get("Time","")).strip()
        kickoff = d + ("T"+tm+"+09:00" if re.fullmatch(r"\d{1,2}:\d{2}", tm) else "")
        conn.execute("""INSERT OR IGNORE INTO matches
            (match_id, competition_id, season_id, kickoff, home_team, away_team,
             status, source_status, primary_source_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (midv,7,sid,kickoff,home,away,"finished","verified_external",4))
        res = "H" if mh > ma else ("A" if mh < ma else "D")
        conn.execute("""INSERT OR REPLACE INTO results
            (match_id, ht_home, ht_away, ft_home, ft_away, result_1x2,
             completed_at, source_status)
            VALUES (?,?,?,?,?,?,?,?)""",
            (midv,None,None,mh,ma,res,d,"verified_external"))
        n += 1
    conn.commit()
    return n

def ingest_k_csv(conn):
    # Never consume the legacy CSV whose Date column contains only kickoff times.
    path = Path("data/auto_results/K_League_1_2026_27_dated.csv")
    if not path.exists():
        raise RuntimeError("dated K League source file missing; run data_pipeline/repair_kleague_dates.py first")
    rows = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "match_id"}
    if not required.issubset(rows.columns):
        raise RuntimeError("dated K League file missing columns: " + str(sorted(required - set(rows.columns))))
    sid = season_id(conn, 6)
    n = 0
    import re
    from datetime import datetime, timezone
    for _, r in rows.iterrows():
        raw_date = str(r.get("Date", "")).strip()
        # Strictly require a calendar-date token; pandas can otherwise interpret HH:MM as today's date.
        if not re.search(r"(?:\\d{4}[-/.]\\d{1,2}[-/.]\\d{1,2}|\\d{1,2}/\\d{1,2}/\\d{4})", raw_date):
            raise RuntimeError("K League Date is not a full calendar date: " + repr(raw_date))
        parsed = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(parsed):
            raise RuntimeError("unparseable K League calendar date: " + repr(raw_date))
        d = parsed.strftime("%Y-%m-%d")
        if not (START <= d <= END):
            continue
        home = str(r.get("HomeTeam", "")).strip()
        away = str(r.get("AwayTeam", "")).strip()
        if not home or not away:
            raise RuntimeError("K League row missing home/away team for " + d)
        midv = str(r.get("match_id", "")).strip()
        if not midv:
            midv = mid("KLEAGUE1", d, home, away)
        tm = str(r.get("Time", "")).strip()
        kickoff = d + ("T" + tm if re.fullmatch(r"\\d{1,2}:\\d{2}", tm) else "")
        ft_h_raw, ft_a_raw = str(r.get("FTHG", "")).strip(), str(r.get("FTAG", "")).strip()
        completed = bool(re.fullmatch(r"\\d+(?:\\.0)?", ft_h_raw) and re.fullmatch(r"\\d+(?:\\.0)?", ft_a_raw))
        ft_h, ft_a = (int(float(ft_h_raw)), int(float(ft_a_raw))) if completed else (None, None)
        conn.execute("""INSERT OR IGNORE INTO matches
            (match_id, competition_id, season_id, kickoff, home_team, away_team,
             status, source_status, primary_source_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (midv, 6, sid, kickoff, home, away,
             "finished" if completed else "scheduled", "verified_external", 6))
        if completed:
            res = "H" if ft_h > ft_a else ("A" if ft_h < ft_a else "D")
            conn.execute("""INSERT OR IGNORE INTO results
                (match_id, ht_home, ht_away, ft_home, ft_away, result_1x2,
                 completed_at, source_status)
                VALUES (?,?,?,?,?,?,?,?)""",
                (midv, None, None, ft_h, ft_a, res, d, "verified_external"))
        provider_event_id = str(r.get("provider_event_id", "")).strip()
        if provider_event_id:
            conn.execute("""INSERT OR IGNORE INTO provider_event_map
                (match_id, provider, event_id, observed_at) VALUES (?,?,?,?)""",
                (midv, "matchesio", provider_event_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        n += 1
    conn.commit()
    return n

def main():
    conn = sqlite3.connect(DB)
    try:
        print("J1 CSV", ingest_j1_csv(conn), "OK")
    except Exception as e:
        print("J1 CSV ERROR:", e)
    try:
        print("K CSV", ingest_k_csv(conn), "OK")
    except Exception as e:
        print("K CSV ERROR:", e)
    for code, (cid, url) in PUBLIC_LEAGUES.items():
        try:
            print(code, ingest(conn, code, cid, url), "OK")
        except Exception as e:
            print(code, "ERROR:", e)
    conn.close()

if __name__ == "__main__":
    main()
