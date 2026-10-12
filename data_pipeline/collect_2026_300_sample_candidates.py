#!/usr/bin/env python3
"""Collect fresh 2026 calendar-year league results into an auditable candidate pool.

This is a data-only step: it does not train or backtest a model, does not touch
V5.1, and does not freeze the 300-match sample. Unknown/unverified rows remain
outside the verified pool.
"""
from __future__ import annotations
import io, json, os, re, hashlib
from datetime import date
from pathlib import Path
import pandas as pd
import requests

START = pd.Timestamp("2026-01-01")
END = pd.Timestamp(date.today())
OUT = Path("data/sample_2026_300")
OUT.mkdir(parents=True, exist_ok=True)
EUROPE = {
    "Premier League": "E0", "LaLiga": "SP1", "Bundesliga": "D1",
    "Serie A": "I1", "Ligue 1": "F1",
}
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; football-sample-audit/1.0)"})

def norm_team(s):
    # Python's Unicode-aware \w preserves Japanese/Korean/Chinese letters and digits.
    # ASCII-only normalization collapsed all J1 team names to empty keys.
    value = str(s).strip().casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)

def read_football_data(code, season_code, league):
    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/{code}.csv"
    r = S.get(url, timeout=45)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), encoding="latin1")
    needed = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if not needed.issubset(df.columns):
        raise ValueError(f"{league} {season_code}: missing columns {sorted(needed-set(df.columns))}")
    df = df[df.FTHG.notna() & df.FTAG.notna()].copy()
    # Football-Data uses day-first dates; fail closed on unparseable dates.
    df["match_date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df[df.match_date.between(START, END)].copy()
    df["league"] = league
    df["season_source"] = season_code
    df["source_primary"] = url
    df["source_secondary"] = ""
    df["verification_status"] = "needs_secondary_verification"
    return df[["match_date","league","season_source","HomeTeam","AwayTeam","FTHG","FTAG","source_primary","source_secondary","verification_status"]]

def main():
    rows, errors = [], []
    # Collect both European seasons; date filtering gives Jan-May from 2526
    # and completed Aug-onward fixtures from 2627.
    for league, code in EUROPE.items():
        for season in ("2526", "2627"):
            try:
                rows.append(read_football_data(code, season, league))
            except Exception as exc:
                errors.append({"league":league,"season":season,"error":str(exc)})

    # J1: calendar-year 2026 spans the official 2026 Special season
    # (100 Year Vision League, Feb-Jun) and the new 2026/27 league season
    # (from Aug). Query both official season IDs; do not accidentally collect
    # only the autumn season and miss the first half of the calendar year.
    j1_queries = [
        ("2026-special", "https://data.j-league.or.jp/SFMS01/search?competition_years=20261&competition_frame_ids=35"),
        ("2026-27", "https://data.j-league.or.jp/SFMS01/search?competition_years=2026&competition_frame_ids=1"),
    ]
    for season_label, j1_url in j1_queries:
        try:
            tables = pd.read_html(j1_url)
            if not tables:
                raise ValueError(f"{season_label}: official J.League page returned no tables")
            raw = tables[0].copy()
            if raw.shape[1] < 8:
                raise ValueError(f"{season_label}: unexpected official J.League table shape {raw.shape}")
            raw = raw.iloc[:, :11].copy()
            raw.columns = ["Season","Competition","Section","Date","Time","HomeTeam","Score","AwayTeam","Stadium","Attendance","TV"][:raw.shape[1]]
            # Keep league competitions only; exclude Levain Cup, Super Cup, and other cups.
            comp = raw["Competition"].astype(str).str.replace(" ", "", regex=False).str.upper()
            is_j1_league = comp.str.contains(r"Ｊ１リーグ|J1LEAGUE|Ｊ１百年構想|J1百年構想|J1 100 YEAR VISION", regex=True, na=False)
            raw = raw[is_j1_league].copy()
            goals = raw["Score"].astype(str).str.extract(r"^\s*(\d+)\s*[-－](?:\s*\(PK\d+[-－]\d+\))?\s*(\d+)\s*$")
            raw["FTHG"] = pd.to_numeric(goals[0], errors="coerce")
            raw["FTAG"] = pd.to_numeric(goals[1], errors="coerce")
            # Penalty shoot-out scores are ignored; use the score before (PK...) only.
            d = raw["Date"].astype(str).str.extract(r"(\d{2,4})/(\d{1,2})/(\d{1,2})")
            year = pd.to_numeric(d[0], errors="coerce")
            year = year.where(year >= 1000, year + 2000)
            raw["match_date"] = pd.to_datetime({"year":year,"month":pd.to_numeric(d[1],errors="coerce"),"day":pd.to_numeric(d[2],errors="coerce")}, errors="coerce")
            raw = raw[raw.match_date.between(START, END) & raw.FTHG.notna() & raw.FTAG.notna()].copy()
            raw["league"] = "J1 League"; raw["season_source"] = season_label
            raw["source_primary"] = j1_url; raw["source_secondary"] = ""
            raw["verification_status"] = "needs_secondary_verification"
            rows.append(raw[["match_date","league","season_source","HomeTeam","AwayTeam","FTHG","FTAG","source_primary","source_secondary","verification_status"]])
        except Exception as exc:
            errors.append({"league":"J1 League","season":season_label,"error":str(exc)})

    # K League 1: public official results page as candidate source, so collection
    # does not depend on a private API key. These rows remain unverified until
    # independently matched against a second source.
    kleague_url = "https://www.kleague.com/record/audienceDetail.do"
    try:
        tables = pd.read_html(kleague_url)
        if not tables:
            raise ValueError("official K League results page returned no tables")
        raw = max(tables, key=lambda t: t.shape[0]).copy()
        raw.columns = [str(x).strip() for x in raw.columns]
        # Current official table schema: season, competition, date, home, opponent,
        # result, stadium, attendance, away, note. Fail closed if it changes.
        def col_match(pattern):
            matches = [col for col in raw.columns if re.search(pattern, col, re.I)]
            return matches[0] if len(matches) == 1 else None
        season_col = col_match(r"시즌|season")
        comp_col = col_match(r"대회명|competition")
        date_col = col_match(r"일자|날짜|date")
        home_col = col_match(r"홈팀|home")
        away_col = col_match(r"원정팀|away")
        score_col = col_match(r"경기결과|결과|result|score")
        required_cols = [season_col,comp_col,date_col,home_col,away_col,score_col]
        if any(x is None for x in required_cols):
            raise ValueError(f"official K League results schema mismatch: {list(raw.columns)}")
        raw = raw[raw[season_col].astype(str).str.contains("2026",na=False) &
                  raw[comp_col].astype(str).str.contains(r"K리그1|K LEAGUE 1",case=False,regex=True,na=False)].copy()
        raw["match_date"] = pd.to_datetime(raw[date_col], errors="coerce")
        score = raw[score_col].astype(str).str.extract(r"(\d+)\s*[:：-]\s*(\d+)")
        raw["FTHG"] = pd.to_numeric(score[0],errors="coerce")
        raw["FTAG"] = pd.to_numeric(score[1],errors="coerce")
        raw = raw[raw.match_date.between(START,END) & raw.FTHG.notna() & raw.FTAG.notna()].copy()
        raw["league"]="K League 1"; raw["season_source"]="official-web-2026"
        raw["HomeTeam"]=raw[home_col].astype(str).str.strip()
        raw["AwayTeam"]=raw[away_col].astype(str).str.strip()
        raw["source_primary"]=kleague_url; raw["source_secondary"]=""
        raw["verification_status"]="needs_secondary_verification"
        rows.append(raw[["match_date","league","season_source","HomeTeam","AwayTeam","FTHG","FTAG","source_primary","source_secondary","verification_status"]])
    except Exception as exc:
        errors.append({"league":"K League 1","error":str(exc)})
        # API fallback only if an explicitly configured official key exists.
        api_key = os.getenv("KLEAGUE_API_KEY")
        if api_key:
            url = "https://api.kleague.com/api/audienceInfo01.do"
            try:
                resp = S.get(url, headers={"authKey":api_key},
                             params={"league_gubun":"1","meet_year":"2026",
                                     "sdate":"2026/01/01","edate":END.strftime("%Y/%m/%d")},
                             timeout=45)
                resp.raise_for_status()
                payload = resp.json()
                api = pd.json_normalize(payload.get("response",{}).get("list",[]))
                required = ["LEAGUE_NAME","GAME_DATE","HOME_TEAM_NAME","AWAY_TEAM_NAME","HOME_GAIN_GOAL","AWAY_GAIN_GOAL"]
                if not set(required).issubset(api.columns):
                    raise ValueError("official K League API schema missing required fields")
                api = api[api.LEAGUE_NAME.astype(str).eq("K리그1")].copy()
                api["match_date"]=pd.to_datetime(api.GAME_DATE,errors="coerce")
                api["FTHG"]=pd.to_numeric(api.HOME_GAIN_GOAL,errors="coerce")
                api["FTAG"]=pd.to_numeric(api.AWAY_GAIN_GOAL,errors="coerce")
                api=api[api.match_date.between(START,END)&api.FTHG.notna()&api.FTAG.notna()].copy()
                api["league"]="K League 1"; api["season_source"]="official-api-2026"
                api["HomeTeam"]=api.HOME_TEAM_NAME; api["AwayTeam"]=api.AWAY_TEAM_NAME
                api["source_primary"]=url; api["source_secondary"]=""
                api["verification_status"]="needs_secondary_verification"
                rows.append(api[["match_date","league","season_source","HomeTeam","AwayTeam","FTHG","FTAG","source_primary","source_secondary","verification_status"]])
            except Exception as api_exc:
                errors.append({"league":"K League 1 API","error":str(api_exc)})

    cols = ["match_date","league","season_source","HomeTeam","AwayTeam","FTHG","FTAG","source_primary","source_secondary","verification_status"]
    pool = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=cols)
    if len(pool):
        pool["match_date"] = pd.to_datetime(pool["match_date"], errors="coerce")
        pool = pool[pool.match_date.between(START, END) & pool.FTHG.notna() & pool.FTAG.notna()].copy()
        pool["home_key"] = pool.HomeTeam.map(norm_team)
        pool["away_key"] = pool.AwayTeam.map(norm_team)
        pool["match_key"] = pool.match_date.dt.strftime("%Y-%m-%d") + "|" + pool.league + "|" + pool.home_key + "|" + pool.away_key
        pool["duplicate_count"] = pool.groupby("match_key")["match_key"].transform("size")
        pool = pool.sort_values(["match_date","league","HomeTeam","AwayTeam"])
    out_csv = OUT / "candidate_pool_2026.csv"
    pool.to_csv(out_csv, index=False, encoding="utf-8-sig")

    # Emit every member of duplicate-key groups; do not silently discard rows.
    duplicate_path = OUT / "duplicate_key_audit.csv"
    if len(pool):
        duplicates = pool[pool.duplicate_count.gt(1)].copy()
        if len(duplicates):
            score_counts = duplicates.groupby("match_key").apply(
                lambda g: g[["FTHG","FTAG"]].drop_duplicates().shape[0],
                include_groups=False
            )
            duplicates["duplicate_score_variants"] = duplicates.match_key.map(score_counts)
            duplicates["duplicate_score_conflict"] = duplicates.duplicate_score_variants.gt(1)
            duplicates = duplicates.sort_values(["match_key","season_source","source_primary"])
        duplicates.to_csv(duplicate_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=cols+["home_key","away_key","match_key","duplicate_count",
                                   "duplicate_score_variants","duplicate_score_conflict"]).to_csv(
            duplicate_path,index=False,encoding="utf-8-sig")

    # Flag identity/score anomalies without automatic correction.
    identity_path = OUT / "date_identity_audit.csv"
    if len(pool):
        audit = pool.copy()
        audit["audit_flags"] = ""
        audit.loc[audit.home_key.eq("") | audit.away_key.eq(""), "audit_flags"] += "empty_team_key;"
        audit.loc[audit.home_key.eq(audit.away_key), "audit_flags"] += "same_home_away_key;"
        audit.loc[(pd.to_numeric(audit.FTHG,errors="coerce") < 0) |
                  (pd.to_numeric(audit.FTAG,errors="coerce") < 0), "audit_flags"] += "negative_score;"
        audit = audit[audit.audit_flags.ne("")].copy()
    else:
        audit = pd.DataFrame(columns=cols+["home_key","away_key","match_key","duplicate_count","audit_flags"])
    audit.to_csv(identity_path, index=False, encoding="utf-8-sig")

    league_counts = {str(k):int(v) for k,v in pool.groupby("league").size().to_dict().items()}
    duplicate_groups = pool[pool.duplicate_count.gt(1)].groupby("match_key") if len(pool) else []
    duplicate_key_count = int(pool.loc[pool.duplicate_count.gt(1),"match_key"].nunique()) if len(pool) else 0
    duplicate_conflict_count = int(
        sum(g[["FTHG","FTAG"]].drop_duplicates().shape[0] > 1 for _,g in duplicate_groups)
    ) if len(pool) else 0
    summary = {
        "date_start":str(START.date()),"date_end":str(END.date()),
        "candidate_rows":int(len(pool)),
        "unique_match_keys":int(pool.match_key.nunique()) if len(pool) else 0,
        "duplicate_rows":int(pool.match_key.duplicated(keep="first").sum()) if len(pool) else 0,
        "duplicate_key_groups":duplicate_key_count,
        "duplicate_groups_with_score_conflict":duplicate_conflict_count,
        "identity_score_anomaly_rows":int(len(audit)),
        "verified_rows":int((pool.verification_status=="verified").sum()) if len(pool) else 0,
        "eligible_for_freeze_rows":0,
        "league_counts":league_counts,"errors":errors,
        "status":"candidate collection only; duplicate/identity audit emitted; no rows frozen; no training/backtest performed",
        "candidate_csv_sha256":hashlib.sha256(out_csv.read_bytes()).hexdigest(),
    }
    (OUT/"collection_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (OUT/"collection_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if len(pool):
        conflict_keys = set(
            pool.loc[pool.duplicate_count.gt(1)].groupby("match_key").filter(
                lambda g: g[["FTHG","FTAG"]].drop_duplicates().shape[0] > 1
            )["match_key"].tolist()
        )
        print("DUPLICATE_SCORE_CONFLICT_DETAILS_BEGIN")
        if conflict_keys:
            view = pool[pool.match_key.isin(conflict_keys)][
                ["match_date","league","season_source","HomeTeam","AwayTeam","FTHG","FTAG","source_primary","match_key"]
            ].sort_values(["match_key","season_source"])
            print(view.head(100).to_json(orient="records",date_format="iso",force_ascii=False))
        else:
            print("[]")
        print("DUPLICATE_SCORE_CONFLICT_DETAILS_END")
        print("LEAGUE_DATE_RANGE_BEGIN")
        ranges = pool.groupby("league").match_date.agg(["min","max","count"]).reset_index()
        print(ranges.to_json(orient="records",date_format="iso",force_ascii=False))
        print("LEAGUE_DATE_RANGE_END")
    if len(pool)==0:
        raise SystemExit("No candidate matches collected")
if __name__ == "__main__":
    main()
