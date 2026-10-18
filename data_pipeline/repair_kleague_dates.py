#!/usr/bin/env python3
"""Rebuild K League 1 fixture dates from grouped public schedule HTML.

The existing CSV is intentionally not overwritten. A new dated file is written only
when a real calendar date can be associated with a match row. Time-only values are
never accepted as dates and provider event IDs are left unresolved if the source
does not expose a stable ID.
"""
import csv
import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from lxml import html

URL = "https://www.matchesio.com/competition/k-league/"
OUT = Path("data/auto_results/K_League_1_2026_27_dated.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 football-model-data-loader/2.0"}
DATE_PATTERNS = (
    re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
)
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
SCORE_RE = re.compile(r"^\s*(\d+)\s*[–—-]\s*(\d+)\s*$")

def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()

def parse_date(text):
    text = clean(text)
    m = DATE_PATTERNS[0].search(text)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = DATE_PATTERNS[1].search(text)
        if not m:
            return None
        mo, d, y = map(int, m.groups())
    try:
        return datetime(y, mo, d).date().isoformat()
    except ValueError:
        return None

def event_id_from_row(row):
    candidates = []
    for el in row.xpath(".//*[@data-event-id or @data-match-id or @data-fixture-id]"):
        for attr in ("data-event-id", "data-match-id", "data-fixture-id"):
            if el.get(attr):
                candidates.append(el.get(attr).strip())
    for a in row.xpath(".//a[@href]"):
        href = a.get("href", "")
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        for key in ("eventId", "event_id", "matchId", "match_id", "fixtureId", "fixture_id", "id"):
            if query.get(key):
                candidates.append(query[key][0])
        m = re.search(r"/(?:match|fixture|event|game)/([A-Za-z0-9_-]+)", parsed.path, re.I)
        if m:
            candidates.append(m.group(1))
    candidates = [x for x in candidates if x]
    return candidates[0] if candidates else ""

def parse_rows(document):
    root = html.fromstring(document)
    current_date = None
    records = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        text = clean(" ".join(el.itertext()))
        if text and len(text) <= 90:
            detected = parse_date(text)
            if detected:
                current_date = detected
        if tag != "tr":
            continue
        cells = [clean(" ".join(cell.itertext())) for cell in el.xpath("./th|./td")]
        if len(cells) < 4:
            continue
        # A match row normally begins with a kickoff time and then matchday/home/away.
        time_match = next((c for c in cells[:2] if TIME_RE.fullmatch(c)), None)
        if not time_match:
            continue
        row_date = next((parse_date(c) for c in cells if parse_date(c)), None)
        if row_date:
            current_date = row_date
        if not current_date:
            continue
        time_index = next(i for i,c in enumerate(cells[:2]) if TIME_RE.fullmatch(c))
        if time_index == 0:
            if len(cells) < 4:
                continue
            home, away = cells[2], cells[3]
            status = cells[6] if len(cells) > 6 else "UNKNOWN"
            score = cells[7] if len(cells) > 7 else ""
        else:
            if len(cells) < 5:
                continue
            home, away = cells[3], cells[4]
            status = cells[7] if len(cells) > 7 else "UNKNOWN"
            score = cells[8] if len(cells) > 8 else ""
        if not home or not away or home.lower() in {"home team","home"} or away.lower() in {"away team","away"}:
            continue
        score_match = SCORE_RE.match(score)
        ft_home = score_match.group(1) if score_match else ""
        ft_away = score_match.group(2) if score_match else ""
        if not ft_home and clean(status).lower() in {"played","finished","full time"}:
            # A finished status without a readable score is not silently treated as a result.
            status = "FINISHED_SCORE_MISSING"
        mid = hashlib.sha256(f"KLEAGUE1|{current_date}|{home.casefold()}|{away.casefold()}".encode("utf-8")).hexdigest()[:24]
        provider_id = event_id_from_row(el)
        records.append({
          "match_id":mid,"provider_event_id":provider_id,
          "identity_status":"VERIFIED" if provider_id else "UNRESOLVED",
          "Date":current_date,"Time":time_match,"HomeTeam":home,"AwayTeam":away,
          "FTHG":ft_home,"FTAG":ft_away,"status":status,
          "source_url":URL,"source_retrieved_at_utc":datetime.utcnow().replace(microsecond=0).isoformat()+"Z"
        })
    unique = {}
    for row in records:
        key = (row["Date"], row["HomeTeam"].casefold(), row["AwayTeam"].casefold())
        unique[key] = row
    return list(unique.values())

def main():
    response = requests.get(URL, timeout=45, headers=HEADERS)
    response.raise_for_status()
    rows = parse_rows(response.text)
    if not rows:
        root = html.fromstring(response.text)
        date_nodes = []
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            node_text = clean(" ".join(el.itertext()))
            if parse_date(node_text) and len(node_text) <= 120:
                date_nodes.append((el.tag, node_text))
        links = [(clean(" ".join(a.itertext())), a.get("href")) for a in root.xpath("//a[@href]") if any(k in (a.get("href") or "").lower() for k in ("csv","json","ics","schedule","match","fixture","api","download"))]
        scripts = [s.get("src") for s in root.xpath("//script[@src]")]
        print({"debug_tr_rows": len(root.xpath("//tr")), "debug_date_nodes": date_nodes[:30], "debug_links": links[:80], "debug_scripts": scripts[:60], "html_length": len(response.text)})
        raise RuntimeError("No date-qualified K League match rows parsed; source layout may have changed")
    bad_dates = [r for r in rows if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["Date"])]
    if bad_dates:
        raise RuntimeError(f"invalid calendar dates in parsed rows: {len(bad_dates)}")
    dates = [r["Date"] for r in rows]
    print({"source":URL,"rows":len(rows),"date_min":min(dates),"date_max":max(dates),
           "provider_event_ids":sum(bool(r["provider_event_id"]) for r in rows),
           "provider_event_id_missing":sum(not r["provider_event_id"] for r in rows)})
    if "--check-only" in sys.argv:
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["match_id","provider_event_id","identity_status","Date","Time","HomeTeam","AwayTeam",
              "FTHG","FTAG","status","source_url","source_retrieved_at_utc"]
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print("WROTE", OUT, "rows", len(rows))

if __name__ == "__main__":
    main()
