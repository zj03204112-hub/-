import io
import os
from datetime import date
from pathlib import Path

import pandas as pd
import requests

OUT = Path("data/auto_results")
OUT.mkdir(parents=True, exist_ok=True)

EUROPE = {
    "Premier League": "E0",
    "LaLiga": "SP1",
    "Bundesliga": "D1",
    "Serie A": "I1",
    "Ligue 1": "F1",
}

def save_normalized(df, league, source):
    # Only completed matches with a final home/away score enter the historical DB.
    if "FTHG" in df.columns and "FTAG" in df.columns:
        df = df[df["FTHG"].notna() & df["FTAG"].notna()].copy()

    cols = [c for c in [
        "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
        "HTHG", "HTAG", "Referee"
    ] if c in df.columns]
    out = df[cols].copy()

    if "Date" in out.columns:
        out["Date"] = out["Date"].astype(str)

    out["league"] = league
    out["source"] = source

    path = OUT / f"{league.replace(' ', '_')}_2026_27.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return len(out)

def fetch_csv(league, code):
    url = f"https://www.football-data.co.uk/mmz4281/2627/{code}.csv"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1")
    return save_normalized(df, league, url)

def fetch_japan():
    # J1 uses the official J.League Data Site. The Football-Data JPN.csv feed
    # is an aggregate Japan feed and is not safe to treat as J1-only.
    url = (
        "https://data.j-league.or.jp/SFMS01/search"
        "?competition_years=2026&competition_frame_ids=1"
        "&tv_relay_station_name="
    )
    tables = pd.read_html(url)
    if not tables:
        raise RuntimeError("J.League Data Site returned no schedule table")

    df = tables[0].copy()
    # Official table columns are: year, competition, section, date, KO, home,
    # score, away, stadium, attendance, broadcast. Keep only final scores.
    if len(df.columns) < 8:
        raise RuntimeError(f"Unexpected J.League table shape: {df.shape}")

    # Normalize by column position because the site uses Japanese headers.
    df = df.iloc[:, :11].copy()
    df.columns = [
        "Season", "Competition", "Section", "Date", "Time",
        "HomeTeam", "Score", "AwayTeam", "Stadium", "Attendance", "TV"
    ][:len(df.columns)]

    score = df["Score"].astype(str).str.extract(r"^(\d+)\s*[-－]\s*(\d+)$")
    df["FTHG"] = pd.to_numeric(score[0], errors="coerce")
    df["FTAG"] = pd.to_numeric(score[1], errors="coerce")
    df = df[df["FTHG"].notna() & df["FTAG"].notna()].copy()

    # Convert the official table into the common database columns.
    out = df[["Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].copy()
    out["league"] = "J1 League"
    out["source"] = url
    out.to_csv(
        OUT / "J1_League_2026_27.csv",
        index=False,
        encoding="utf-8-sig",
    )
    return len(out)

def fetch_kleague():
    try:
        url = "https://www.matchesio.com/competition/k-league/"
        tables = pd.read_html(url)
        for t in tables:
            cols = {str(c).lower() for c in t.columns}
            if not (any("home" in c for c in cols) and any("away" in c for c in cols)):
                continue
            rename = {}
            for c in t.columns:
                lc = str(c).lower()
                if "home" in lc: rename[c] = "HomeTeam"
                elif "away" in lc: rename[c] = "AwayTeam"
                elif "date" in lc: rename[c] = "Date"
                elif "score" in lc or "result" in lc: rename[c] = "Score"
                elif "time" in lc: rename[c] = "Time"
            t = t.rename(columns=rename)
            if {"Date","HomeTeam","AwayTeam","Score"}.issubset(t.columns):
                m = t["Score"].astype(str).str.extract(r"(\d+)\s*[-–:]\s*(\d+)")
                t["FTHG"] = pd.to_numeric(m[0], errors="coerce")
                t["FTAG"] = pd.to_numeric(m[1], errors="coerce")
                t = t[t["FTHG"].notna() & t["FTAG"].notna()].copy()
                if len(t):
                    return save_normalized(t, "K League 1", url)
    except Exception as e:
        print(f"Public K League fallback failed: {e}")

    api_key = os.getenv("KLEAGUE_API_KEY")
    if not api_key:
        print("K League 1: skipped (KLEAGUE_API_KEY not configured)")
        return 0

    url = "https://api.kleague.com/api/audienceInfo01.do"
    headers = {"authKey": api_key}
    params = {"league_gubun": "1", "meet_year": "2026",
              "sdate": "2026/01/01", "edate": date.today().strftime("%Y/%m/%d")}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("response", {}).get("list", [])
    if not rows:
        raise RuntimeError(f"K League API returned no rows: {payload}")
    df = pd.json_normalize(rows)
    if "LEAGUE_NAME" not in df.columns:
        raise RuntimeError("K League response has no LEAGUE_NAME field")
    df = df[df["LEAGUE_NAME"].astype(str).eq("K리그1")].copy()
    df = df.rename(columns={"GAME_DATE":"Date","HOME_TEAM_NAME":"HomeTeam",
                            "AWAY_TEAM_NAME":"AwayTeam","HOME_GAIN_GOAL":"FTHG",
                            "AWAY_GAIN_GOAL":"FTAG"})
    return save_normalized(df, "K League 1", url)

def main():
    counts = {}
    failures = []

    for league, code in EUROPE.items():
        try:
            counts[league] = fetch_csv(league, code)
        except Exception as e:
            failures.append(f"{league}: {e}")

    try:
        counts["J1 League"] = fetch_japan()
    except Exception as e:
        failures.append(f"J1 League: {e}")

    try:
        counts["K League 1"] = fetch_kleague()
    except Exception as e:
        failures.append(f"K League 1: {e}")

    print("=== Completed league match counts ===")
    for league in [*EUROPE.keys(), "J1 League", "K League 1"]:
        print(f"{league}: {counts.get(league, 0)}")

    # The six non-K-League feeds are required for this pipeline.
    required = [*EUROPE.keys(), "J1 League"]
    missing = [league for league in required if counts.get(league, 0) == 0]

    if failures:
        print("=== Errors ===")
        for failure in failures:
            print(f" - {failure}")

    if missing:
        raise SystemExit(
            "Required league data missing/empty: " + ", ".join(missing)
        )

if __name__ == "__main__":
    main()
