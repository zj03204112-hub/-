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
    url = "https://www.football-data.co.uk/new/JPN.csv"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1")

    # If the feed provides a division field, retain only J1.
    for c in ["League", "Division", "Div"]:
        if c in df.columns:
            values = df[c].astype(str).str.upper()
            j1 = df[values.isin({"J1", "J1 LEAGUE", "1"})].copy()
            if not j1.empty:
                df = j1
            break

    return save_normalized(df, "J1 League", url)

def fetch_kleague():
    api_key = os.getenv("KLEAGUE_API_KEY")
    if not api_key:
        print("K League 1: skipped (KLEAGUE_API_KEY not configured)")
        return 0

    url = "https://api.kleague.com/api/audienceInfo01.do"
    headers = {"authKey": api_key}
    params = {
        "league_gubun": "1",
        "meet_year": "2026",
        "sdate": "2026/01/01",
        "edate": date.today().strftime("%Y/%m/%d"),
    }

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

    df = df.rename(columns={
        "GAME_DATE": "Date",
        "HOME_TEAM_NAME": "HomeTeam",
        "AWAY_TEAM_NAME": "AwayTeam",
        "HOME_GAIN_GOAL": "FTHG",
        "AWAY_GAIN_GOAL": "FTAG",
    })

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
