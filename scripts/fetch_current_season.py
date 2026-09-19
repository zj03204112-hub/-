import io
import pandas as pd
import requests
from pathlib import Path

OUT = Path("data/auto_results")
OUT.mkdir(parents=True, exist_ok=True)

EUROPE = {
    "Premier League": "E0",
    "LaLiga": "SP1",
    "Bundesliga": "D1",
    "Serie A": "I1",
    "Ligue 1": "F1",
}

def fetch_csv(league, code):
    url = f"https://www.football-data.co.uk/mmz4281/2627/{code}.csv"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1")
    cols = [c for c in ["Date","HomeTeam","AwayTeam","FTHG","FTAG","HTHG","HTAG","Referee"] if c in df.columns]
    out = df[cols].copy()
    out["league"] = league
    out.to_csv(OUT / f"{league.replace(' ','_')}_2026_27.csv", index=False, encoding="utf-8-sig")
    return len(out)

def main():
    counts = {}
    for league, code in EUROPE.items():
        try:
            counts[league] = fetch_csv(league, code)
        except Exception as e:
            print(f"{league}: {e}")
    print(counts)

if __name__ == "__main__":
    main()
