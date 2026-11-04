#!/usr/bin/env python3
"""Scan the market for systematic pricing bias, then validate out of sample.

Instead of trying to out-predict the closing line, this looks for segments where the
opening price is systematically wrong: a slice of matches where the realised frequency of
an outcome differs from the de-vigged implied probability by more than chance allows.

Protocol:
  discovery  2023-24 + 2024-25 seasons
  validation 2025-26 + 2026-27 seasons
  multiple testing controlled with Benjamini-Hochberg on the discovery p-values
  a segment only counts if it is significant in discovery AND keeps its sign and magnitude
  in validation, and survives the vig (ROI measured at the observed opening prices)

Dimensions scanned: implied-probability bucket, league, calendar month, rest-days bucket,
Asian handicap line, and the over/under 2.5 market.

Usage:
    python data_pipeline/scan_market_bias.py
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO_ROOT, "model_validation", "market_bias_scan.json")
URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
FEEDS = {"E0": "EPL", "D1": "BUNDESLIGA", "SP1": "LALIGA", "F1": "LIGUE1", "I1": "SERIEA"}
DISCOVERY = ("2324", "2425")
VALIDATION = ("2526", "2627")
MIN_N = 60
ALPHA = 0.10

OUTCOMES = ("H", "D", "A")


def log(message: str) -> None:
    print(message, flush=True)


def fetch(season: str, code: str) -> str | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(1, 3):
        try:
            request = urllib.request.Request(URL.format(season=season, code=code),
                                             headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(request, timeout=45) as response:
                return response.read().decode("utf-8-sig", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            if attempt == 2:
                return None
    return None


def devig(values: list[float]) -> list[float] | None:
    if any(not math.isfinite(v) or v <= 1.0 for v in values):
        return None
    inverse = [1.0 / v for v in values]
    total = sum(inverse)
    return [x / total for x in inverse]


def probability_bucket(p: float, width: float) -> str:
    lower = math.floor(p / width) * width
    return f"{lower:.2f}-{lower + width:.2f}"


def parse(text: str, league: str, season: str, history: dict) -> list[dict]:
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
            home_goals, away_goals = int(raw.get("FTHG")), int(raw.get("FTAG"))
            odds = [float(raw.get("B365H")), float(raw.get("B365D")), float(raw.get("B365A"))]
        except (TypeError, ValueError):
            continue
        probabilities = devig(odds)
        if not probabilities:
            continue
        try:
            line = float(raw.get("AHh"))
        except (TypeError, ValueError):
            line = None
        try:
            over = devig([float(raw.get("B365>2.5")), float(raw.get("B365<2.5"))])
        except (TypeError, ValueError):
            over = None
        try:
            ah_odds = {"home": float(raw.get("B365AHH")), "away": float(raw.get("B365AHA"))}
            ah = devig([ah_odds["home"], ah_odds["away"]])
        except (TypeError, ValueError):
            ah, ah_odds = None, None
        try:
            ou_odds = {"over": float(raw.get("B365>2.5")), "under": float(raw.get("B365<2.5"))}
        except (TypeError, ValueError):
            ou_odds = None
        actual = "H" if home_goals > away_goals else ("D" if home_goals == away_goals else "A")

        rest = {}
        for side, team in (("home", raw.get("HomeTeam", "").strip()),
                           ("away", raw.get("AwayTeam", "").strip())):
            previous = history.get(team)
            rest[side] = None
            if previous:
                delta = (date.fromisoformat(match_date) - date.fromisoformat(previous)).days
                rest[side] = delta
            history[team] = match_date

        rows.append({
            "season": season, "league": league, "date": match_date,
            "month": match_date[:7], "actual": actual,
            "home_goals": home_goals, "away_goals": away_goals,
            "p": dict(zip(OUTCOMES, probabilities)), "odds": dict(zip(OUTCOMES, odds)),
            "line": line, "over": over[0] if over else None,
            "ah_home": ah[0] if ah else None, "ah_odds": ah_odds, "ou_odds": ou_odds,
            "rest_home": rest["home"], "rest_away": rest["away"],
        })
    return rows


def bucket_rest(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value <= 3:
        return "2-3d"
    if value <= 5:
        return "4-5d"
    if value <= 7:
        return "6-7d"
    return "8d+"


def segments(row: dict) -> list[tuple[str, str, str]]:
    """(dimension, value, outcome) triples this row belongs to."""
    out = []
    for outcome in OUTCOMES:
        probability = row["p"][outcome]
        width = 0.02 if outcome == "D" else 0.05
        out.append(("prob", probability_bucket(probability, width), outcome))
        out.append(("league", row["league"], outcome))
        out.append(("month", row["month"], outcome))
        if row["rest_home"] is not None:
            out.append(("rest_home", bucket_rest(row["rest_home"]), outcome))
        if row["rest_away"] is not None:
            out.append(("rest_away", bucket_rest(row["rest_away"]), outcome))
    if row["line"] is not None and row["ah_home"] is not None:
        line_key = f"{row['line']:+.2f}"
        for side in ("home", "away"):
            out.append(("line", line_key, side))
    if row["over"] is not None:
        for side in ("over", "under"):
            out.append(("ou25", probability_bucket(row["over"], 0.05), side))
    return out


def aggregate(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    buckets: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"n": 0, "implied": 0.0, "hits": 0, "return": 0.0})
    for row in rows:
        for dimension, value, label in segments(row):
            key = (dimension, value, label)
            entry = buckets[key]
            if dimension == "line":
                margin = (row["home_goals"] - row["away_goals"]) + row["line"]
                if margin == 0:
                    continue                      # push: no stake, no information
                probability = row["ah_home"] if label == "home" else 1 - row["ah_home"]
                hit = margin > 0 if label == "home" else margin < 0
                entry["n"] += 1
                entry["implied"] += probability
                entry["hits"] += int(hit)
                if row["ah_odds"]:
                    entry["return"] += (row["ah_odds"][label] - 1) if hit else -1.0
            elif dimension == "ou25":
                total = row["home_goals"] + row["away_goals"]
                probability = row["over"] if label == "over" else 1 - row["over"]
                hit = total > 2.5 if label == "over" else total < 2.5
                entry["n"] += 1
                entry["implied"] += probability
                entry["hits"] += int(hit)
                if row["ou_odds"]:
                    entry["return"] += (row["ou_odds"][label] - 1) if hit else -1.0
            else:
                entry["n"] += 1
                entry["implied"] += row["p"][label]
                hit = row["actual"] == label
                entry["hits"] += int(hit)
                entry["return"] += (row["odds"][label] - 1) if hit else -1.0
    return buckets


def two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def summarise(buckets: dict, min_n: int = MIN_N) -> list[dict]:
    out = []
    for (dimension, value, label), entry in buckets.items():
        n = entry["n"]
        if n < min_n:
            continue
        implied = entry["implied"] / n
        realised = entry["hits"] / n
        if not 0 < implied < 1:
            continue
        se = math.sqrt(implied * (1 - implied) / n)
        z = (realised - implied) / se if se else 0.0
        out.append({"dimension": dimension, "value": value, "outcome": label, "n": n,
                    "implied": round(implied, 4), "realised": round(realised, 4),
                    "edge": round(realised - implied, 4), "z": round(z, 2),
                    "p": two_sided_p(z),
                    "roi": round(entry["return"] / n, 4)})
    return out


def benjamini_hochberg(items: list[dict], alpha: float = ALPHA) -> None:
    ordered = sorted(items, key=lambda x: x["p"])
    m = len(ordered)
    for index, item in enumerate(ordered, start=1):
        item["q"] = round(item["p"] * m / index, 4)
    running = 1.0
    for item in reversed(ordered):
        running = min(running, item["q"])
        item["q"] = round(running, 4)


def main() -> int:
    rows: list[dict] = []
    for season in DISCOVERY + VALIDATION:
        for code, league in FEEDS.items():
            text = fetch(season, code)
            if not text:
                log(f"  {season}/{code}: download failed")
                continue
            history: dict[str, str] = {}
            rows.extend(parse(text, league, season, history))
    log(f"matches: {len(rows)}")

    discovery = [r for r in rows if r["season"] in DISCOVERY]
    validation = [r for r in rows if r["season"] in VALIDATION]
    dev_buckets = aggregate(discovery)
    val_buckets = aggregate(validation)
    dev = summarise(dev_buckets)
    benjamini_hochberg(dev)
    val_index = {(v["dimension"], v["value"], v["outcome"]): v for v in summarise(val_buckets)}

    findings = []
    for item in dev:
        if item["q"] > ALPHA:
            continue
        check = val_index.get((item["dimension"], item["value"], item["outcome"]))
        record = dict(item)
        record["validation"] = check if check else {"n": 0}
        record["confirmed"] = bool(check and check["n"] >= MIN_N
                                   and (check["edge"] * item["edge"] > 0)
                                   and abs(check["edge"]) >= abs(item["edge"]) * 0.5)
        findings.append(record)

    findings.sort(key=lambda x: x["q"])
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "definition": (
            "Systematic bias scan on de-vigged opening prices. Outcomes are grouped into "
            "segments; each segment is tested for realised frequency differing from implied "
            "probability. Discovery uses the first two seasons, validation the last two, and "
            "Benjamini-Hochberg controls the discovery false discovery rate at 10%. A finding "
            "is only 'confirmed' when the validation segment keeps the sign, at least half the "
            "magnitude, and has at least 60 matches."),
        "discovery_seasons": list(DISCOVERY), "validation_seasons": list(VALIDATION),
        "counts": {"matches": len(rows), "discovery": len(discovery),
                   "validation": len(validation),
                   "segments_tested": len(dev), "discovery_significant": len(findings),
                   "confirmed": sum(1 for f in findings if f["confirmed"])},
        "findings": findings[:40],
        "note": "ROI is flat-stake at the observed opening price, before any commission.",
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    log(f"  segments tested: {len(dev)}, discovery significant at q<={ALPHA}: {len(findings)}, "
        f"confirmed out of sample: {payload['counts']['confirmed']}")
    for item in findings[:12]:
        v = item["validation"]
        log(f"  {item['dimension']:9s} {item['value']:12s} {item['outcome']:5s} "
            f"n={item['n']:5d} edge={item['edge']:+.4f} q={item['q']:.4f} roi={item['roi']:+.3f} "
            f"| val n={v.get('n', 0):4d} edge={v.get('edge', 0):+.4f} "
            f"{'CONFIRMED' if item['confirmed'] else ''}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
