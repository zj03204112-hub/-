import io
import os
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
    cols = [c for c in [
        "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
        "HTHG", "HTAG", "Referee"
    ] if c in df.columns]
    out = df[cols].copy()
    out["league"] = league
    out["source"] = source
    out.to_csv(
        OUT / f"{league.replace(' ', '_')}_2026_27.csv",
        index=False,
        encoding="utf-8-sig",
    )
    return len(out)

def fetch_csv(league, code):
    url = f"https://www.football-data.co.uk/mmz4281/2627/{code}.csv"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1")
    return save_normalized(df, league, url)

def fetch_japan():
    # Football-Data's Japan feed is the current downloadable Japan results file.
    # Keep only J1 rows when a division/league column is supplied by the feed.
    url = "https://www.football-data.co.uk/new/JPN.csv"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1")

    for c in ["League", "Division", "Div"]:
        if c in df.columns:
            values = df[c].astype(str).str.upper()
            j1 = df[values.isin({"J1", "J1 LEAGUE", "1"})].copy()
            if not j1.empty:
                df = j1
            break

    return save_normalized(df, "J1 League", url)

def fetch_kleague():
    # K League official API requires an API key. The workflow reads it from
    # GitHub Actions secret KLEAGUE_API_KEY. If the secret is not configured,
    # Europe + Japan still update and the job does not fail.
    api_key = os.getenv("KLEAGUE_API_KEY")
    if not api_key:
        print("K League: skipped (KLEAGUE_API_KEY secret not configured)")
        return 0

    # K League's official audience endpoint includes the match master data
    # plus final home/away goals and supports a date range.
    from datetime import date

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
    df = df[df["LEAGUE_NAME"].astype(str).eq("K리그1")].copy()

    rename = {
        "GAME_DATE": "Date",
        "HOME_TEAM_NAME": "HomeTeam",
        "AWAY_TEAM_NAME": "AwayTeam",
        "HOME_GAIN_GOAL": "FTHG",
        "AWAY_GAIN_GOAL": "FTAG",
    }
    df = df.rename(columns=rename)

    # The audience endpoint is already restricted to completed records in
    # practice, but keep an explicit score check for database safety.
    df = df[df["FTHG"].notna() & df["FTAG"].notna()].copy()

    return save_normalized(df, "K League 1", url)

def main():
    counts = {}
    for league, code in EUROPE.items():
        try:
            counts[league] = fetch_csv(league, code)
        except Exception as e:
            print(f"{league}: {e}")

    for league, fn in [
        ("J1 League", fetch_japan),
        ("K League 1", fetch_kleague),
    ]:
        try:
            counts[league] = fn()
        except Exception as e:
            print(f"{league}: {e}")

    print(counts)

if __name__ == "__main__":
    main()
