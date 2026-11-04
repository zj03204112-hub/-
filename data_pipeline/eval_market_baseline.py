#!/usr/bin/env python3
"""Market baseline: de-vigged opening bookmaker odds vs our lambda models.

The benchmark every feature model has to be measured against. Uses football-data.co.uk
opening prices (B365 and the market average), de-vigs them to H/D/A probabilities and
scores them on exactly the same holdout matches as eval_lambda_baselines.py.

Matching is by league, date (one day tolerance) and team-name similarity, so no manual
alias table is needed; unmatched rows are reported instead of silently dropped.

Usage:
    python data_pipeline/eval_market_baseline.py
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(REPO_ROOT, "football_model_database.sqlite")
OUT = os.path.join(REPO_ROOT, "model_validation", "market_baseline_comparison.json")
HOLDOUT_CSV = os.path.join(REPO_ROOT, "model_validation", "lambda_baseline_holdout.csv")
COMPARISON = os.path.join(REPO_ROOT, "model_validation", "lambda_baseline_comparison.json")

# football-data code -> our league code
FEEDS = {
    "SP1": "LALIGA",
    "E0": "EPL",
    "D1": "BUNDESLIGA",
    "I1": "SERIEA",
    "F1": "LIGUE1",
    "N1": "EREDIVISIE",
}
BASE_URL = "https://www.football-data.co.uk/mmz4281/2627/{code}.csv"

DROP_TOKENS = {"fc", "cf", "sc", "ac", "as", "ss", "us", "cd", "sd", "ud", "rc", "club",
               "de", "the", "afc", "1", "1900", "04", "05", "07", "09", "1899", "1913"}


def log(message: str) -> None:
    print(message, flush=True)


def norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return text


def tokens(value: str) -> set[str]:
    text = re.sub(r"[^a-z0-9 ]", " ", norm(value))
    return {t for t in text.split() if t and t not in DROP_TOKENS}


ALIASES = {
    # our LaLiga names (laliga.com) vs football-data short forms
    "rayo": "vallecano", "vallecano": "vallecano",
    "racing": "santander", "santander": "santander",
    "espanol": "espanyol", "espanyol": "espanyol",
    "deportivo": "lacoruna", "lacoruna": "lacoruna",
    "atletico": "athmadrid", "athmadrid": "athmadrid",
    "athletic": "athbilbao", "athbilbao": "athbilbao",
    # Bundesliga
    "mgladbach": "monchengladbach", "monchengladbach": "monchengladbach",
    "munich": "munchen",
}


def canonical(value: str) -> str:
    text = re.sub(r"[^a-z0-9]", "", norm(value))
    return ALIASES.get(text, text)


def similarity(a: str, b: str) -> float:
    import difflib
    ca, cb = canonical(a), canonical(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    best = 0.0
    ta, tb = tokens(a), tokens(b)
    if ta and tb:
        best = max(best, len(ta & tb) / min(len(ta), len(tb)))
    if len(ca) >= 4 and len(cb) >= 4 and (ca in cb or cb in ca):
        best = max(best, 0.9)
    best = max(best, difflib.SequenceMatcher(None, ca, cb).ratio())
    return best


def fetch_csv(code: str) -> str | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(BASE_URL.format(code=code),
                                             headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(request, timeout=40) as response:
                return response.read().decode("utf-8-sig", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            if attempt == 3:
                return None
    return None


def devig(odds: list[float]) -> list[float] | None:
    if any(not math.isfinite(o) or o <= 1.0 for o in odds):
        return None
    inv = [1.0 / o for o in odds]
    total = sum(inv)
    if total <= 0:
        return None
    return [x / total for x in inv]


def parse_feed(code: str, text: str) -> list[dict]:
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        date_text = (raw.get("Date") or "").strip()
        try:
            match_date = datetime.strptime(date_text, "%d/%m/%Y").date().isoformat()
        except ValueError:
            try:
                match_date = datetime.strptime(date_text, "%d/%m/%y").date().isoformat()
            except ValueError:
                continue
        record = {"code": code, "date": match_date,
                  "home": (raw.get("HomeTeam") or "").strip(),
                  "away": (raw.get("AwayTeam") or "").strip()}
        for label, columns in (("b365", ("B365H", "B365D", "B365A")),
                               ("average", ("AvgH", "AvgD", "AvgA")),
                               ("pinnacle", ("PSH", "PSD", "PSA"))):
            try:
                odds = [float(raw.get(c)) for c in columns]
            except (TypeError, ValueError):
                continue
            probs = devig(odds)
            if probs:
                record[label] = {"H": probs[0], "D": probs[1], "A": probs[2], "odds": odds}
        if "b365" in record or "average" in record:
            rows.append(record)
    return rows


def devig_two(home_odds: float, away_odds: float) -> float | None:
    """Home cover probability from a two-way Asian handicap market."""
    if not (math.isfinite(home_odds) and math.isfinite(away_odds)):
        return None
    if home_odds <= 1.0 or away_odds <= 1.0:
        return None
    a, b = 1.0 / home_odds, 1.0 / away_odds
    total = a + b
    return a / total if total > 0 else None


def parse_ah_feed(text: str) -> list[dict]:
    """Rows with the Asian handicap line and de-vigged home cover probabilities."""
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        date_text = (raw.get("Date") or "").strip()
        try:
            match_date = datetime.strptime(date_text, "%d/%m/%Y").date().isoformat()
        except ValueError:
            try:
                match_date = datetime.strptime(date_text, "%d/%m/%y").date().isoformat()
            except ValueError:
                continue
        try:
            line = float(raw.get("AHh"))
        except (TypeError, ValueError):
            continue
        record = {"date": match_date, "home": (raw.get("HomeTeam") or "").strip(),
                  "away": (raw.get("AwayTeam") or "").strip(), "line": line}
        try:
            record["p_b365"] = devig_two(float(raw.get("B365AHH")), float(raw.get("B365AHA")))
        except (TypeError, ValueError):
            record["p_b365"] = None
        try:
            record["p_avg"] = devig_two(float(raw.get("AvgAHH")), float(raw.get("AvgAHA")))
        except (TypeError, ValueError):
            record["p_avg"] = None
        if record["p_b365"] is not None or record["p_avg"] is not None:
            rows.append(record)
    return rows


def score(probabilities: dict[str, float], actual: str) -> tuple[int, float, float]:
    y = {k: 0.0 for k in probabilities}
    y[actual] = 1.0
    correct = int(max(probabilities, key=probabilities.get) == actual)
    brier = sum((probabilities[k] - y[k]) ** 2 for k in probabilities)
    logloss = -math.log(max(1e-12, probabilities[actual]))
    return correct, brier, logloss


def summarise(rows: list[tuple[int, float, float]]) -> dict:
    if not rows:
        return {"n": 0}
    n = len(rows)
    return {"n": n,
            "accuracy": round(sum(r[0] for r in rows) / n, 4),
            "brier": round(sum(r[1] for r in rows) / n, 4),
            "logloss": round(sum(r[2] for r in rows) / n, 4)}


def main() -> int:
    import sqlite3
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    fixtures = [dict(r) for r in connection.execute("""
        SELECT match_key, league_code, match_date, home_team, away_team, score
        FROM dsl_matches WHERE score LIKE '%-%'""")]

    # holdout definition must match eval_lambda_baselines.py exactly
    cutoff = None
    if os.path.exists(COMPARISON):
        cutoff = json.load(open(COMPARISON, encoding="utf-8")).get("settings", {}).get("holdout_start")
    holdout_models: dict[str, dict[str, dict]] = defaultdict(dict)
    if os.path.exists(HOLDOUT_CSV):
        for row in csv.DictReader(open(HOLDOUT_CSV, encoding="utf-8")):
            holdout_models[row["match_key"]][row["model"]] = row
    log(f"holdout cutoff: {cutoff}, holdout matches: {len(holdout_models)}")

    by_league_date: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for fixture in fixtures:
        by_league_date[(fixture["league_code"], fixture["match_date"])].append(fixture)

    matched: list[dict] = []
    unmatched: list[dict] = []
    for code, league in FEEDS.items():
        text = fetch_csv(code)
        if not text:
            log(f"  {code}: download failed")
            continue
        rows = parse_feed(code, text)
        league_hits = 0
        for row in rows:
            best, best_score = None, 0.0
            for delta in (0, 1, -1):
                from datetime import date, timedelta
                day = (date.fromisoformat(row["date"]) + timedelta(days=delta)).isoformat()
                for candidate in by_league_date.get((league, day), []):
                    score_home = similarity(row["home"], candidate["home_team"])
                    score_away = similarity(row["away"], candidate["away_team"])
                    if min(score_home, score_away) > best_score:
                        best, best_score = candidate, min(score_home, score_away)
            if best and best_score >= 0.55:
                match = dict(row)
                match.update({"match_key": best["match_key"], "our_home": best["home_team"],
                              "our_away": best["away_team"], "name_score": round(best_score, 2)})
                matched.append(match)
                league_hits += 1
            else:
                unmatched.append(row)
        log(f"  {code}: parsed {len(rows)}, matched {league_hits}")

    results: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    by_league: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    holdout_pairs = 0
    for row in matched:
        key = row["match_key"]
        if key not in holdout_models:
            continue
        holdout_pairs += 1
        actual = None
        fixture = next((f for f in fixtures if f["match_key"] == key), None)
        if not fixture:
            continue
        fh, fa = (int(x) for x in fixture["score"].split("-"))
        actual = "H" if fh > fa else ("D" if fh == fa else "A")
        for label in ("b365", "average", "pinnacle"):
            if label in row:
                probs = {k: row[label][k] for k in ("H", "D", "A")}
                results[label].append(score(probs, actual))
                by_league[label][fixture["league_code"]].append(score(probs, actual))
        # our models on the same matches
        models = holdout_models[key]
        for name in ("goals_lambda", "xg_lambda", "league_average"):
            if name in models:
                columns = {"H": "p_home", "D": "p_draw", "A": "p_away"}
                p = {k: float(models[name][columns[k]]) for k in ("H", "D", "A")}
                results[name].append(score(p, actual))
                by_league[name][fixture["league_code"]].append(score(p, actual))

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "Market baseline from football-data.co.uk opening prices, de-vigged per book "
            "and scored on the same holdout matches as the lambda models. Opening odds are a "
            "conservative stand-in for a T-12h snapshot: they carry less information than the "
            "closing line, so any gap against the market here is a lower bound."),
        "holdout_start": cutoff,
        "feeds": FEEDS,
        "coverage": {"holdout_matches": len(holdout_models),
                     "matched_market_matches": holdout_pairs,
                     "unmatched_feed_rows": len(unmatched)},
        "metrics": {label: summarise(rows) for label, rows in results.items()},
        "by_league": {label: {lg: summarise(rows) for lg, rows in per.items()}
                      for label, per in by_league.items()},
        "unmatched_sample": unmatched[:12],
        "caveats": [
            "No odds feed for K League 1, J1 or the A-League, so those matches are excluded "
            "from the comparison rather than compared against nothing.",
            "Opening prices, not T-12h snapshots; see definition.",
            "Holdout is roughly one third of a three-month window, so treat differences "
            "under 0.005 Brier as noise.",
        ],
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    order = ["average", "b365", "pinnacle", "league_average", "goals_lambda", "xg_lambda"]
    for label in order:
        m = payload["metrics"].get(label)
        if m and m.get("n"):
            log(f"  {label:15s} n={m['n']:4d} acc={m['accuracy']:.3f} "
                f"brier={m['brier']:.4f} logloss={m['logloss']:.4f}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
