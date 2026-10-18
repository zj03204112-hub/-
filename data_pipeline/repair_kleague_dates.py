#!/usr/bin/env python3
"""Rebuild K League 1 calendar dates and provider IDs from public match ICS links.

The legacy CSV is not overwritten. Match dates and kickoff times are read from each
match's public ICS event, not inferred from the old time-only Date column. Rows that
lack a stable event ID or valid DTSTART remain unresolved and are not imported.
"""
import csv
import hashlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

import requests
from lxml import html

URL = "https://www.matchesio.com/competition/k-league/"
BASE = "https://www.matchesio.com"
OUT = Path("data/auto_results/K_League_1_2026_27_dated.csv")
QUARANTINE = Path("data/auto_results/K_League_1_undated_quarantine.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 football-model-data-loader/2.1"}
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
SCORE_RE = re.compile(r"^\s*(\d+)\s*[–—-]\s*(\d+)\s*$")
KST = ZoneInfo("Asia/Seoul")

KLEAGUE_TEAM_ALIASES = {
    "fc seoul":"서울", "seoul":"서울",
    "incheon united":"인천", "incheon":"인천",
    "gwangju fc":"광주", "gwangju":"광주",
    "jeonbuk hyundai motors":"전북", "jeonbuk motors":"전북", "jeonbuk":"전북",
    "ulsan hd fc":"울산", "ulsan hd":"울산", "ulsan hyundai":"울산", "ulsan":"울산",
    "pohang steelers":"포항", "pohang":"포항",
    "daejeon hana citizen":"대전", "daejeon":"대전",
    "gangwon fc":"강원", "gangwon":"강원",
    "suwon fc":"수원FC", "suwon":"수원FC",
    "gimcheon sangmu":"김천", "gimcheon":"김천",
    "daegu fc":"대구", "daegu":"대구",
    "jeju sk fc":"제주", "jeju united":"제주", "jeju":"제주",
    "fc anyang":"안양", "anyang":"안양",
    "bucheon fc 1995":"부천", "bucheon":"부천",
    "seongnam fc":"성남", "seongnam":"성남",
    "suwon samsung bluewings":"수원삼성"
}

def canonical_k_team(name):
    key = re.sub(r"\s+", " ", str(name or "").strip().casefold())
    return KLEAGUE_TEAM_ALIASES.get(key, key)

def stable_k_match_id(match_date, home, away):
    return hashlib.sha1(
        f"KLEAGUE1|{match_date}|{canonical_k_team(home)}|{canonical_k_team(away)}".encode("utf-8")
    ).hexdigest()[:20]


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()

def event_url_from_row(row):
    for a in row.xpath(".//a[@href]"):
        href = a.get("href", "")
        if ".ics" in href.lower() and "/match/" in href.lower():
            return href if href.startswith("http") else BASE + href
    return ""

def event_id_from_row(row):
    url = event_url_from_row(row)
    if not url:
        return ""
    path = urlparse(url).path
    match = re.search(r"/match/([A-Za-z0-9_-]+)\.ics$", path, re.I)
    if match:
        return match.group(1)
    query = parse_qs(urlparse(url).query)
    for key in ("eventId", "event_id", "matchId", "match_id", "fixtureId", "fixture_id", "id"):
        if query.get(key):
            return query[key][0]
    return ""

def parse_rows(document):
    root = html.fromstring(document)
    records = []
    for row in root.xpath("//tr"):
        if not isinstance(row.tag, str):
            continue
        cells = [clean(" ".join(cell.itertext())) for cell in row.xpath("./th|./td")]
        if len(cells) < 4:
            continue
        time_text = next((value for value in cells[:2] if TIME_RE.fullmatch(value)), "")
        if not time_text:
            continue
        time_index = next(i for i,value in enumerate(cells[:2]) if TIME_RE.fullmatch(value))
        if time_index == 0:
            home, away = (cells[2], cells[3]) if len(cells) >= 4 else ("", "")
            status = cells[6] if len(cells) > 6 else "UNKNOWN"
            score = cells[7] if len(cells) > 7 else ""
        else:
            home, away = (cells[3], cells[4]) if len(cells) >= 5 else ("", "")
            status = cells[7] if len(cells) > 7 else "UNKNOWN"
            score = cells[8] if len(cells) > 8 else ""
        if not home or not away or home.lower() in {"home team","home"} or away.lower() in {"away team","away"}:
            continue
        provider_id = (row.get("data-match-id") or "").strip() or event_id_from_row(row)
        date_value = (row.get("data-day") or "").strip()
        time_value = time_text
        time_quality = "TIME_TEXT_ONLY"
        epoch = (row.get("data-time") or "").strip()
        if epoch:
            try:
                seconds = float(epoch)
                if seconds > 10_000_000_000:
                    seconds /= 1000
                local_dt = datetime.fromtimestamp(seconds, timezone.utc).astimezone(KST)
                date_value = local_dt.date().isoformat()
                time_value = local_dt.strftime("%H:%M")
                time_quality = "UNIX_EPOCH_CONVERTED_TO_KST"
            except (ValueError, OverflowError, OSError):
                pass
        if date_value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
            date_value = ""
        score_match = SCORE_RE.match(score)
        ft_home = score_match.group(1) if score_match else ""
        ft_away = score_match.group(2) if score_match else ""
        if not ft_home and clean(status).lower() in {"played","finished","full time"}:
            status = "FINISHED_SCORE_MISSING"
        mid = stable_k_match_id(date_value, home, away) if date_value else ""
        records.append({
          "match_id":mid,"provider_event_id":provider_id,
          "identity_status":"VERIFIED" if provider_id else "UNRESOLVED",
          "Date":date_value,"Time":time_value,"kickoff_timezone":"Asia/Seoul",
          "kickoff_time_quality":time_quality,"HomeTeam":home,"AwayTeam":away,
          "FTHG":ft_home,"FTAG":ft_away,"status":status,
          "source_url":URL,"event_ics_url":event_url_from_row(row),
          "source_retrieved_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds")
        })
    unique = {}
    for record in records:
        key = record["provider_event_id"] or (
            record["Date"], record["HomeTeam"].casefold(), record["AwayTeam"].casefold()
        )
        unique[key] = record
    return list(unique.values()), root

def parse_ics_datetime(ics_text):
    for line in ics_text.splitlines():
        if not line.upper().startswith("DTSTART"):
            continue
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        value = value.strip()
        if re.fullmatch(r"\d{8}", value):
            dt = datetime.strptime(value, "%Y%m%d").replace(tzinfo=KST)
            return dt, "DATE_ONLY_LOCAL"
        m = re.fullmatch(r"(\d{8})T(\d{6})(Z)?", value)
        if not m:
            continue
        dt = datetime.strptime(m.group(1)+m.group(2), "%Y%m%d%H%M%S")
        tzid = re.search(r"TZID=([^;:]+)", left, re.I)
        if m.group(3):
            dt = dt.replace(tzinfo=timezone.utc).astimezone(KST)
            return dt, "UTC_CONVERTED_TO_KST"
        if tzid:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tzid.group(1))).astimezone(KST)
                return dt, "TZID_CONVERTED_TO_KST"
            except Exception:
                return None, "UNKNOWN_TZID"
        # A floating DTSTART has no timezone evidence. Preserve its calendar date/time
        # but mark the assumption explicitly instead of calling it UTC.
        return dt.replace(tzinfo=KST), "FLOATING_ASSUMED_KST"
    return None, "DTSTART_MISSING"

def fetch_event(row):
    url = row.get("event_ics_url")
    if not url or not row.get("provider_event_id"):
        return row, "PROVIDER_EVENT_ID_MISSING"
    try:
        response = requests.get(url, timeout=20, headers=HEADERS)
        response.raise_for_status()
        dt, quality = parse_ics_datetime(response.text)
        if dt is None:
            return row, quality
        row["Date"] = dt.date().isoformat()
        row["Time"] = dt.strftime("%H:%M")
        row["kickoff_timezone"] = "Asia/Seoul"
        row["kickoff_time_quality"] = quality
        row["source_retrieved_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row["identity_status"] = "VERIFIED"
        row["match_id"] = stable_k_match_id(row["Date"], row["HomeTeam"], row["AwayTeam"])
        return row, ""
    except Exception as exc:
        return row, "ICS_FETCH_ERROR:" + type(exc).__name__

def main():
    response = requests.get(URL, timeout=45, headers=HEADERS)
    response.raise_for_status()
    rows, root = parse_rows(response.text)
    if "--inspect-load-more" in sys.argv:
        nodes = []
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            node_text = clean(" ".join(el.itertext()))
            if "load more matches" in node_text.lower() and len(node_text) < 160:
                nodes.append({"tag":el.tag,"text":node_text,"attributes":dict(el.attrib)})
        print({"load_more_nodes":nodes[:20]})
        for src in root.xpath("//script[@src]/@src"):
            if "competition_detail" not in src:
                continue
            js_url = src if src.startswith("http") else BASE + src
            js_response = requests.get(js_url, timeout=20, headers=HEADERS)
            js_response.raise_for_status()
            body = js_response.text
            snippets = []
            for term in ("load more", "loadMore", "load-more", "buildChunkUrl", "data-fetch-url", "fetch(", "ajax", "api/", "offset", "page"):
                for match in list(re.finditer(re.escape(term), body, re.I))[:5]:
                    snippets.append({"term":term,"snippet":body[max(0,match.start()-180):match.end()+260]})
            print({"script_url":js_url,"script_length":len(body),"snippets":snippets[:40]})
        button = root.xpath("//*[@data-matches-load-more]")
        if button:
            endpoint = button[0].get("data-fetch-url", "")
            page = button[0].get("data-next-page", "2")
            target = endpoint if endpoint.startswith("http") else BASE + endpoint
            probe = requests.get(target, params={"page":page}, timeout=20,
                                 headers={**HEADERS,"Accept":"application/json","X-Requested-With":"XMLHttpRequest"})
            print({"pagination_probe_url":probe.url,"status":probe.status_code,
                   "content_type":probe.headers.get("Content-Type"),
                   "body_prefix":probe.text[:2500]})
        return
    if not rows:
        raise RuntimeError("No K League match rows or event ICS links found on public source")
    button = root.xpath("//*[@data-matches-load-more]")
    page_coverage_complete = not bool(button)
    if button:
        endpoint = button[0].get("data-fetch-url", "")
        next_page = int(button[0].get("data-next-page", "2") or "2")
        target = endpoint if endpoint.startswith("http") else BASE + endpoint
        seen = {r["provider_event_id"] for r in rows if r["provider_event_id"]}
        for _ in range(30):
            page_response = requests.get(
                target, params={"page":next_page}, timeout=30,
                headers={**HEADERS,"Accept":"application/json","X-Requested-With":"XMLHttpRequest"}
            )
            page_response.raise_for_status()
            payload = page_response.json()
            fragment = payload.get("html", "")
            if not fragment.strip():
                page_coverage_complete = True
                break
            extra, _ = parse_rows(fragment)
            fresh = [r for r in extra if not r["provider_event_id"] or r["provider_event_id"] not in seen]
            if not fresh:
                page_coverage_complete = True
                break
            rows.extend(fresh)
            seen.update(r["provider_event_id"] for r in fresh if r["provider_event_id"])
            if payload.get("has_more") is False or payload.get("hasMore") is False:
                page_coverage_complete = True
                break
            next_page = int(payload.get("next_page") or payload.get("nextPage") or (next_page + 1))
        else:
            page_coverage_complete = False
    # Fall back to the event ICS only for rows whose calendar date is absent.
    missing_dates = [r for r in rows if not r.get("Date") and r.get("event_ics_url")]
    if missing_dates:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fetch_event, row) for row in missing_dates]
            replacements = {}
            for future in as_completed(futures):
                row, error = future.result()
                replacements[row.get("provider_event_id") or row.get("HomeTeam","")] = (row,error)
            rows = [replacements.get(r.get("provider_event_id") or r.get("HomeTeam",""), (r,""))[0] for r in rows]
    dated, unresolved = [], []
    for row in rows:
        if not row.get("Date") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["Date"]):
            row["missing_reason"] = "CALENDAR_DATE_UNRESOLVED"
            unresolved.append(row)
            continue
        if not row.get("match_id"):
            row["match_id"] = stable_k_match_id(row["Date"], row["HomeTeam"], row["AwayTeam"])
        if not row.get("provider_event_id"):
            row["identity_status"] = "UNRESOLVED"
        dated.append(row)
    dated.sort(key=lambda r: (r["Date"], r["Time"], r["HomeTeam"], r["AwayTeam"]))
    dates = [r["Date"] for r in dated]
    coverage_status = "COMPLETE_PAGINATION" if page_coverage_complete else "PARTIAL_PAGE_LOAD_MORE_PRESENT"
    for row in dated:
        row["source_coverage_status"] = coverage_status
    print({
      "source":URL,"page_rows":len(rows),"dated_rows":len(dated),
      "unresolved_rows":len(unresolved),"date_min":min(dates) if dates else None,
      "date_max":max(dates) if dates else None,
      "provider_event_ids":sum(bool(r["provider_event_id"]) for r in dated),
      "source_coverage_status":coverage_status
    })
    if "--check-only" in sys.argv:
        if not dated:
            raise RuntimeError("No dated K League rows were recovered")
        return
    fields = ["match_id","provider_event_id","identity_status","Date","Time","kickoff_timezone",
              "kickoff_time_quality","HomeTeam","AwayTeam","FTHG","FTAG","status","source_url",
              "event_ics_url","source_retrieved_at_utc","source_coverage_status"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(dated)
    qfields = ["provider_event_id","Date","Time","HomeTeam","AwayTeam","source_url",
               "event_ics_url","source_retrieved_at_utc","missing_reason"]
    with QUARANTINE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=qfields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(unresolved)
    print("WROTE", OUT, "rows", len(dated), "QUARANTINE", QUARANTINE, "rows", len(unresolved))

if __name__ == "__main__":
    main()
