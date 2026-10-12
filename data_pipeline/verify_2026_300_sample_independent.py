#!/usr/bin/env python3
"""Cross-check candidate results against SofaScore, an independent results provider.

Fail-closed: no match is marked verified unless league, local match date (within
one day to account for timezone display), both teams, finished status, and the
90-minute/full-time score all match. Penalty shoot-out scores are not used.
This script never freezes samples and never runs model training/backtests.
"""
from __future__ import annotations
import json, re, time, hashlib
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import requests

ROOT = Path("data/sample_2026_300")
INPUT = ROOT / "candidate_pool_2026.csv"
OUT = ROOT / "independent_source_verification.csv"
SUMMARY = ROOT / "independent_source_verification_summary.json"
BASE = "https://www.sofascore.com/api/v1/sport/football/scheduled-events/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; football-independent-verifier/1.0)"}
LEAGUE_TERMS = {
    "Premier League": ("premier league",),
    "LaLiga": ("laliga", "la liga", "primera division"),
    "Bundesliga": ("bundesliga",),
    "Serie A": ("serie a",),
    "Ligue 1": ("ligue 1",),
    "K League 1": ("k league 1", "k league classic"),
    "J1 League": ("j1 league", "j1 100 year vision", "100 year vision league"),
}
# Name aliases bridge official Korean/Japanese scripts and SofaScore English labels.
ALIASES = {
    # Japan
    "ジェフユナイテッド千葉":"jef united chiba", "ジェフユナイテッド市原・千葉":"jef united chiba",
    "水戸ホーリーホック":"mito hollyhock", "v・ファーレン長崎":"v varen nagasaki",
    "Ｖ・ファーレン長崎":"v varen nagasaki", "清水エスパルス":"shimizu s pulse",
    "東京ヴェルディ":"tokyo verdy", "サンフレッチェ広島":"sanfrecce hiroshima",
    "川崎フロンターレ":"kawasaki frontale", "名古屋グランパス":"nagoya grampus",
    "柏レイソル":"kashiwa reysol", "ヴィッセル神戸":"vissel kobe",
    "横浜f・マリノス":"yokohama f marinos", "横浜Ｆ・マリノス":"yokohama f marinos",
    "浦和レッズ":"urawa reds", "鹿島アントラーズ":"kashima antlers",
    "ガンバ大阪":"gamba osaka", "セレッソ大阪":"cerezo osaka",
    "京都サンガf.c.":"kyoto sanga", "京都サンガＦ.Ｃ.":"kyoto sanga",
    "アビスパ福岡":"avispa fukuoka", "アルビレックス新潟":"albirex niigata",
    "湘南ベルマーレ":"shonan bellmare", "東京fc":"fc tokyo", "fc東京":"fc tokyo",
    "町田ゼルビア":"machida zelvia", "ファジアーノ岡山":"fagiano okayama",
    "ロアッソ熊本":"roasso kumamoto", "ジュビロ磐田":"jubilo iwata",
    "ベガルタ仙台":"vegalta sendai", "モンテディオ山形":"montedio yamagata",
    "徳島ヴォルティス":"tokushima vort is", "大分トリニータ":"oita trinita",
    # Korea
    "전북":"jeonbuk hyundai motors", "전북현대":"jeonbuk hyundai motors",
    "전북 현대 모터스":"jeonbuk hyundai motors", "울산":"ulsan hd",
    "울산 hd":"ulsan hd", "서울":"fc seoul", "fc서울":"fc seoul",
    "포항":"pohang steelers", "포항 스틸러스":"pohang steelers",
    "대전":"daejeon hana citizen", "대전 하나 시티즌":"daejeon hana citizen",
    "광주":"gwangju fc", "광주 fc":"gwangju fc", "강원":"gangwon fc",
    "강원 fc":"gangwon fc", "제주":"jeju sk", "제주 sk":"jeju sk",
    "김천":"gimcheon sangmu", "김천 상무":"gimcheon sangmu",
    "인천":"incheon united", "인천 유나이티드":"incheon united",
    "수원 fc":"suwon fc", "안양":"fc anyang", "fc 안양":"fc anyang",
    "부천":"bucheon fc 1995", "부천 fc 1995":"bucheon fc 1995",
    "대구":"daegu fc", "대구 fc":"daegu fc",
}

def norm(value):
    s = str(value).strip().casefold()
    s = re.sub(r"[\u200b\ufeff]", "", s)
    s = re.sub(r"[\W_]+", "", s, flags=re.UNICODE)
    return s

ALIAS_NORM = {norm(k): norm(v) for k, v in ALIASES.items()}

def team_key(value):
    n = norm(value)
    return ALIAS_NORM.get(n, n)

def close_team(a, b):
    a, b = team_key(a), team_key(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.88

def tournament_text(event):
    t = event.get("tournament") or {}
    u = event.get("uniqueTournament") or t.get("uniqueTournament") or {}
    c = t.get("category") or {}
    return " ".join(str(x or "") for x in [t.get("name"), u.get("name"), c.get("name")]).casefold()

def league_match(league, event):
    txt = tournament_text(event)
    return any(term in txt for term in LEAGUE_TERMS.get(league, ()))

def event_date(event):
    ts = event.get("startTimestamp")
    if not ts:
        return None
    # Compare both UTC and Asia/Tokyo/Seoul calendar dates downstream by allowing +/- 1 day.
    return datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()

def event_score(event):
    status = (event.get("status") or {}).get("type", "")
    if status not in {"finished", "after_penalties"}:
        return None
    hs = event.get("homeScore") or {}
    aws = event.get("awayScore") or {}
    # current is the regulation/full-time result in SofaScore; avoid penalty score fields.
    h, a = hs.get("current"), aws.get("current")
    if h is None or a is None:
        return None
    try:
        return int(h), int(a)
    except (TypeError, ValueError):
        return None

def fetch_day(day):
    url = BASE.format(day)
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            payload = r.json()
            events = payload.get("events")
            if not isinstance(events, list):
                raise ValueError("SofaScore response has no events list")
            return day, events, None
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0 * (attempt + 1))
    return day, [], last_error

def main():
    if not INPUT.exists():
        raise SystemExit(f"Missing candidate input: {INPUT}")
    df = pd.read_csv(INPUT)
    required = {"match_date","league","HomeTeam","AwayTeam","FTHG","FTAG","source_primary"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Candidate input missing columns: {sorted(missing)}")
    df["match_date"] = pd.to_datetime(df.match_date, errors="coerce")
    # Query each candidate date and adjacent dates. This accounts for source display
    # timezone boundaries; date tolerance alone never permits team/score mismatches.
    days = set()
    for d in df.match_date.dropna().dt.date.unique():
        for offset in (-1, 0, 1):
            days.add((pd.Timestamp(d) + pd.Timedelta(days=offset)).strftime("%Y-%m-%d"))
    fetched, errors = {}, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_day, day): day for day in sorted(days)}
        for future in as_completed(futures):
            day, events, error = future.result()
            fetched[day] = events
            if error:
                errors.append({"date":day,"error":error})
    rows = []
    for idx, row in df.iterrows():
        date_value = row.match_date.date().isoformat() if pd.notna(row.match_date) else ""
        try:
            expected_score = (int(row.FTHG), int(row.FTAG))
        except (TypeError, ValueError):
            expected_score = None
        candidates = []
        for offset in (-1, 0, 1):
            day = (pd.Timestamp(date_value) + pd.Timedelta(days=offset)).strftime("%Y-%m-%d") if date_value else ""
            for ev in fetched.get(day, []):
                if not league_match(str(row.league), ev):
                    continue
                home = (ev.get("homeTeam") or {}).get("name", "")
                away = (ev.get("awayTeam") or {}).get("name", "")
                if close_team(row.HomeTeam, home) and close_team(row.AwayTeam, away):
                    score = event_score(ev)
                    candidates.append((ev, home, away, score, day))
        status = "not_found_secondary"
        matched = None
        if len(candidates) == 1:
            ev, home, away, score, query_day = candidates[0]
            matched = (ev, home, away, score, query_day)
            if score is None:
                status = "secondary_not_finished_or_score_missing"
            elif expected_score is not None and score == expected_score:
                status = "verified"
            else:
                status = "score_conflict"
        elif len(candidates) > 1:
            # Multiple candidate events are ambiguous, even if one score matches.
            status = "ambiguous_secondary_match"
        else:
            # Distinguish provider/API outage from a genuine no-match.
            if errors:
                status = "secondary_lookup_incomplete"
        ev, home, away, score, query_day = matched if matched else ({}, "", "", None, "")
        rows.append({
            "candidate_row": int(idx) + 1,
            "candidate_date": date_value,
            "league": row.league,
            "candidate_home": row.HomeTeam,
            "candidate_away": row.AwayTeam,
            "candidate_home_goals": expected_score[0] if expected_score else "",
            "candidate_away_goals": expected_score[1] if expected_score else "",
            "candidate_source": row.source_primary,
            "secondary_provider": "SofaScore",
            "secondary_match_id": ev.get("id", ""),
            "secondary_date_utc": event_date(ev) or "",
            "secondary_home": home,
            "secondary_away": away,
            "secondary_home_goals": score[0] if score else "",
            "secondary_away_goals": score[1] if score else "",
            "secondary_tournament": tournament_text(ev) if ev else "",
            "secondary_url": f"https://www.sofascore.com/event/{ev.get('id')}" if ev.get("id") else "",
            "verification_status": status,
            "identity_date_score_all_match": status == "verified",
            "review_note": "" if status == "verified" else "No automatic correction; manual review required",
        })
    result = pd.DataFrame(rows)
    result.to_csv(OUT, index=False, encoding="utf-8-sig")
    counts = {str(k):int(v) for k,v in result.verification_status.value_counts(dropna=False).items()}
    league_counts = {}
    for league, grp in result.groupby("league"):
        league_counts[str(league)] = {
            "candidate_rows": int(len(grp)),
            "verified": int((grp.verification_status == "verified").sum()),
            "score_conflict": int((grp.verification_status == "score_conflict").sum()),
            "not_found_or_incomplete": int((~grp.verification_status.isin(["verified","score_conflict","ambiguous_secondary_match","secondary_not_finished_or_score_missing"])).sum()),
            "ambiguous": int((grp.verification_status == "ambiguous_secondary_match").sum()),
        }
    summary = {
        "candidate_rows": int(len(result)),
        "verified_rows": int((result.verification_status == "verified").sum()),
        "eligible_for_freeze_rows": 0,
        "status_counts": counts,
        "league_counts": league_counts,
        "provider": "SofaScore independent secondary source",
        "provider_days_requested": len(days),
        "provider_days_failed": len(errors),
        "provider_errors": errors[:50],
        "verification_csv_sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "status": "verification audit only; never freezes sample; no training/backtest performed",
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # Fail the workflow if the secondary source is unavailable, while still retaining audit artifacts.
    if not fetched or len(errors) == len(days):
        raise SystemExit("Independent source unavailable for all requested dates")

if __name__ == "__main__":
    main()
