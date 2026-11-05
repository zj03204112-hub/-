#!/usr/bin/env python3
"""Half-time / full-time (半全场) probabilities.

Two blocks:

  market   the official jingcai hafu pool prices all nine half-time/full-time combinations;
           de-vigging them gives the market's implied probabilities for tonight's card
  base     historical frequencies of the same nine combinations, read from the 500.com daily
           archive (match.xml carries halfscore)

Comparing the two shows where the market's half-time view differs from what actually happened.

Usage:
    python data_pipeline/predict_half_full.py                 # tonight's card
    python data_pipeline/predict_half_full.py --base-days 60  # also rebuild the base rates
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO_ROOT, "model_validation", "half_full_probabilities.json")
API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
ARCHIVE = "https://www.500.com/static/public/jczq/xml/hisdata/{year}/{mmdd}/match.xml"
ARCHIVE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://trade.500.com/",
                   "Accept": "application/xml,text/xml,*/*"}

# hafu keys are <half><full>: h = home lead, d = level, a = away lead
COMBOS = {
    "hh": "胜胜", "hd": "胜平", "ha": "胜负",
    "dh": "平胜", "dd": "平平", "da": "平负",
    "ah": "负胜", "ad": "负平", "aa": "负负",
}


def log(message: str) -> None:
    print(message, flush=True)


def fetch_card() -> list[dict]:
    url = API + "?" + urllib.parse.urlencode({"poolCode": "had,hhad,hafu", "channel": "c"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    rows = []
    for group in (payload.get("value") or {}).get("matchInfoList") or []:
        for item in group.get("subMatchList") or []:
            hafu = item.get("hafu") or {}
            odds = {}
            for key in COMBOS:
                try:
                    value = float(hafu.get(key))
                except (TypeError, ValueError):
                    continue
                if value > 1.0:
                    odds[key] = value
            if len(odds) != 9:
                continue
            inverse = {k: 1.0 / v for k, v in odds.items()}
            total = sum(inverse.values())
            rows.append({
                "num": item.get("matchNumStr"), "league": item.get("leagueAbbName"),
                "home": item.get("homeTeamAbbName"), "away": item.get("awayTeamAbbName"),
                "kickoff": f"{item.get('matchDate')} {item.get('matchTime')}",
                "odds": odds,
                "probs": {k: round(v / total, 4) for k, v in inverse.items()},
                "overround": round(total - 1.0, 4),
            })
    return rows


def base_rates(days: int) -> dict:
    """Historical frequencies of the nine combinations from the daily archive."""
    counter: Counter = Counter()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    today = date(2026, 11, 5)
    fetched = 0
    for offset in range(days):
        day = today - timedelta(days=offset)
        url = ARCHIVE.format(year=day.strftime("%Y"), mmdd=day.strftime("%m%d"))
        try:
            with opener.open(urllib.request.Request(url, headers=ARCHIVE_HEADERS),
                             timeout=25) as response:
                body = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            continue
        if not body.lstrip().startswith(b"<?xml"):
            time.sleep(5)
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue
        for node in root.iter("match"):
            half = (node.get("halfscore") or "").strip()
            full = (node.get("score") or "").strip()
            if ":" not in half or ":" not in full:
                continue
            try:
                hg, ag = (int(x) for x in half.split(":"))
                fh, fa = (int(x) for x in full.split(":"))
            except ValueError:
                continue
            if hg < 0 or ag < 0 or fh < 0 or fa < 0:
                continue
            first = "h" if hg > ag else ("d" if hg == ag else "a")
            second = "h" if fh > fa else ("d" if fh == fa else "a")
            counter[first + second] += 1
            fetched += 1
        time.sleep(1.5)
    total = sum(counter.values())
    return {"matches": fetched,
            "rates": {k: round(counter[k] / total, 4) for k in COMBOS} if total else {},
            "counts": {k: counter[k] for k in COMBOS}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-days", type=int, default=0,
                        help="also scan this many archive days for historical base rates")
    args = parser.parse_args()

    card = fetch_card()
    log(f"on sale: {len(card)} matches")
    payload = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "definition": (
                   "Half-time/full-time probabilities from the official jingcai hafu pool, "
                   "de-vigged. The nine combinations are coded <half><full> with h home lead, "
                   "d level, a away lead."),
               "matches": card}

    if args.base_days:
        log(f"scanning {args.base_days} archive days for historical base rates "
            f"(slow, about 1.5s per day) ...")
        payload["base_rates"] = base_rates(args.base_days)
        log(f"  historical matches: {payload['base_rates']['matches']}")
        for key, label in COMBOS.items():
            rate = payload["base_rates"]["rates"].get(key)
            if rate is not None:
                log(f"    {label}: {100 * rate:.1f}%")

    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(OUT, "a", encoding="utf-8").write("\n")

    # print the most likely combinations per match
    for match in card[:40]:
        ordered = sorted(match["probs"].items(), key=lambda kv: -kv[1])[:4]
        text = "  ".join(f"{COMBOS[k]} {100 * v:.1f}%" for k, v in ordered)
        log(f"  {match['num']} {match['home'][:6]}-{match['away'][:6]:6s} "
            f"走水 {100 * match['overround']:+.1f}%  {text}")
    log(f"report: {os.path.relpath(OUT, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
