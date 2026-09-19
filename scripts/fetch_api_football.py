import os
from datetime import datetime, timezone
from pathlib import Path
import requests
import pandas as pd

API_KEY = os.getenv("API_FOOTBALL_KEY")
OUT = Path("data/api_football")
OUT.mkdir(parents=True, exist_ok=True)

LEAGUES = {
    39: "Premier League",
    140: "LaLiga",
    78: "Bundesliga",
    135: "Serie A",
    61: "Ligue 1",
    98: "J1 League",
    292: "K League 1",
}

BASE = "https://v3.football.api-sports.io"

def get(endpoint, params):
    r = requests.get(
        f"{BASE}/{endpoint}",
        headers={"x-apisports-key": API_KEY},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data

def fetch_fixtures(league_id, league_name):
    data = get("fixtures", {"league": league_id, "season": 2026})
    rows = []
    for item in data.get("response", []):
        fixture = item["fixture"]
        teams = item["teams"]
        goals = item["goals"]
        rows.append({
            "fixture_id": fixture["id"],
            "date_utc": fixture["date"],
            "status": fixture["status"]["short"],
            "league_id": league_id,
            "league": league_name,
            "home_team": teams["home"]["name"],
            "away_team": teams["away"]["name"],
            "home_goals": goals["home"],
            "away_goals": goals["away"],
            "source": "API-Football",
            "source_checked_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows

def main():
    if not API_KEY:
        raise SystemExit("API_FOOTBALL_KEY is not configured")

    all_rows = []
    successes = 0
    failures = []

    for league_id, league_name in LEAGUES.items():
        try:
            rows = fetch_fixtures(league_id, league_name)
            all_rows.extend(rows)
            successes += 1
            print(f"{league_name}: ok ({len(rows)} fixtures)")
        except Exception as e:
            failures.append(f"{league_name}: {e}")
            print(f"{league_name}: {e}")

    if not all_rows:
        print("no data returned")
        raise SystemExit(
            "API-Football returned no fixtures. "
            "Check season coverage/plan before treating this workflow as successful."
        )

    df = pd.DataFrame(all_rows)
    df = df.sort_values(["date_utc", "league", "home_team"])
    df.to_csv(OUT / "fixtures_2026.csv", index=False, encoding="utf-8-sig")
    print(f"saved {len(df)} fixtures from {successes}/{len(LEAGUES)} leagues")

    if failures:
        print("Warnings:")
        for failure in failures:
            print(f" - {failure}")

if __name__ == "__main__":
    main()
