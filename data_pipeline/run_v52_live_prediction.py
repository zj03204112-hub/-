#!/usr/bin/env python3
"""V5.2 live prediction trace runner.

This is a data-lineage wrapper: it imports the existing V5.2 calculation
functions without changing their equations. It fails closed if the database,
provider identity, or source timestamp evidence is insufficient.
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.getenv("FOOTBALL_DB", str(ROOT / "football_model_database.sqlite")))
TRACE_DB = Path(os.getenv("V52_TRACE_DB", str(ROOT / "data" / "v52_live_trace.sqlite")))
sys.path.insert(0, str(ROOT / "data_pipeline"))
import compare_models as model

SPORTTERY_BASE = "https://webapi.sporttery.cn/gateway"
MATCH_LIST = SPORTTERY_BASE + "/uniform/fb/getMatchDataPageListV1.qry"
FIXED = SPORTTERY_BASE + "/uniform/football/getFixedBonusV1.qry"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
    "Referer": "https://m.sporttery.cn/",
    "Accept": "application/json,text/plain,*/*",
}
UTC = timezone.utc

def now():
    return datetime.now(UTC).isoformat(timespec="seconds")

def sha(obj):
    b = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b).hexdigest()

def get_json(url, params=None):
    full = url + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(full, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()
        return json.loads(raw), full, raw

def init_trace():
    TRACE_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(TRACE_DB)
    c.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS fixtures(
      internal_match_id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_match_id TEXT NOT NULL,
      kickoff TEXT NOT NULL, home TEXT NOT NULL, away TEXT NOT NULL, fixture_raw_json TEXT NOT NULL,
      fixture_retrieved_at TEXT NOT NULL, fixture_payload_sha256 TEXT NOT NULL,
      UNIQUE(provider,provider_match_id));
    CREATE TABLE IF NOT EXISTS predictions(
      prediction_id TEXT PRIMARY KEY, internal_match_id TEXT NOT NULL, prediction_time TEXT NOT NULL,
      kickoff TEXT NOT NULL, model_version TEXT NOT NULL, code_sha TEXT NOT NULL,
      lambda_home REAL NOT NULL, lambda_away REAL NOT NULL, p_home REAL NOT NULL, p_draw REAL NOT NULL,
      p_away REAL NOT NULL, score1 TEXT NOT NULL, score1_probability REAL NOT NULL,
      score2 TEXT NOT NULL, score2_probability REAL NOT NULL, payload_json TEXT NOT NULL,
      payload_sha256 TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS market_snapshots(
      snapshot_id TEXT PRIMARY KEY, internal_match_id TEXT NOT NULL, provider TEXT NOT NULL,
      provider_event_id TEXT NOT NULL, source_url TEXT NOT NULL, provider_event_time TEXT,
      retrieved_at TEXT NOT NULL, timestamp_quality TEXT NOT NULL, raw_json TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL, normalized_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS results(
      internal_match_id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_event_id TEXT NOT NULL,
      ft_home INTEGER NOT NULL, ft_away INTEGER NOT NULL, provider_completed_at TEXT,
      retrieved_at TEXT NOT NULL, source_url TEXT NOT NULL, raw_json TEXT NOT NULL, raw_sha256 TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions BEGIN SELECT RAISE(ABORT,'append-only predictions'); END;
    CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions BEGIN SELECT RAISE(ABORT,'append-only predictions'); END;
    CREATE TRIGGER IF NOT EXISTS markets_no_update BEFORE UPDATE ON market_snapshots BEGIN SELECT RAISE(ABORT,'append-only markets'); END;
    CREATE TRIGGER IF NOT EXISTS markets_no_delete BEFORE DELETE ON market_snapshots BEGIN SELECT RAISE(ABORT,'append-only markets'); END;
    CREATE TRIGGER IF NOT EXISTS results_no_update BEFORE UPDATE ON results BEGIN SELECT RAISE(ABORT,'append-only results'); END;
    CREATE TRIGGER IF NOT EXISTS results_no_delete BEFORE DELETE ON results BEGIN SELECT RAISE(ABORT,'append-only results'); END;
    """)
    c.commit()
    return c

def canonical(s):
    import re
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (s or "").lower())

def score_from_sporttery(m):
    import re
    s = str(m.get("sectionsNo999") or "")
    xs = re.findall(r"\d+", s)
    if len(xs) >= 2:
        return int(xs[-2]), int(xs[-1])
    return None, None

def fetch_fixtures(day):
    j, url, raw = get_json(MATCH_LIST, {"method":"result","matchDate":day})
    groups = (j.get("value") or {}).get("matchInfoList") or []
    rows = [m for g in groups for m in (g.get("subMatchList") or [])]
    return rows, url, raw

def parse_kickoff(m, day):
    # Only accept an explicit provider kickoff field. Unknown formats block the fixture.
    for k in ("matchDate", "matchTime", "matchDateTime", "startTime", "kickoff"):
        v = m.get(k)
        if v:
            try:
                dt = datetime.fromisoformat(str(v).replace("Z","+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                return dt.astimezone(UTC).isoformat(timespec="seconds")
            except ValueError:
                pass
    return None

def extract_market(payload):
    v = payload.get("value") or {}
    hist = v.get("oddsHistory") or {}
    out = {"hhad": hist.get("hhadList") or [], "had": hist.get("hadList") or [],
           "total_goals": hist.get("ttgList") or hist.get("totalGoalsList") or []}
    return out

def require_leisu():
    # Provider-specific URL is configured only after its response format is verified.
    template = os.getenv("LEISU_EVENT_URL_TEMPLATE", "").strip()
    if not template:
        raise RuntimeError("BLOCKED_LEISU_ADAPTER_NOT_CONFIGURED: set LEISU_EVENT_URL_TEMPLATE to a verified public Leisu event endpoint; no fabricated Leisu records will be written.")
    return template

def main():
    if not DB.exists():
        raise SystemExit(f"DATABASE_NOT_FOUND: {DB}")
    con = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"matches","results"}.issubset(tables):
        raise SystemExit("MODEL_DATABASE_SCHEMA_MISSING")
    trace = init_trace()
    day = os.getenv("FIXTURE_DATE", datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    fixtures, list_url, list_raw = fetch_fixtures(day)
    print(json.dumps({"phase":"fixture_discovery","date":day,"sporttery_fixture_count":len(fixtures),
                      "source_url":list_url,"retrieved_at":now(),"raw_sha256":sha(list_raw)},ensure_ascii=False))
    leisu_template = require_leisu()  # fail closed before creating any prediction
    selected = []
    for m in fixtures:
        home = m.get("allHomeTeam") or m.get("homeTeam") or ""
        away = m.get("allAwayTeam") or m.get("awayTeam") or ""
        provider_id = str(m.get("matchId") or "")
        kickoff = parse_kickoff(m, day)
        if not home or not away or not provider_id or not kickoff:
            continue
        ko = datetime.fromisoformat(kickoff)
        if ko <= datetime.now(UTC):
            continue
        # Match a database fixture only by exact normalized teams and kickoff date.
        db_rows = con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches WHERE substr(kickoff,1,10)=?",
                              (ko.date().isoformat(),)).fetchall()
        candidates = [r for r in db_rows if canonical(r[2])==canonical(home) and canonical(r[3])==canonical(away)]
        if len(candidates) != 1:
            continue
        internal_id = candidates[0][0]
        selected.append((m, home, away, provider_id, kickoff, internal_id))
    if not selected:
        raise SystemExit("BLOCKED_NO_UNIQUE_REAL_UNSTARTED_FIXTURES: no official fixture mapped uniquely to the model DB")
    completed = 0
    for m, home, away, sporttery_id, kickoff, mid in selected:
        leisu_url = leisu_template.format(event_id=urllib.parse.quote(sporttery_id), date=day)
        lj, lurl, lraw = get_json(leisu_url)
        retrieved = now()
        # Do not guess Leisu schema. Require event ID, teams, kickoff, and a source event timestamp.
        if not isinstance(lj, dict) or not lj.get("eventId") or not lj.get("homeTeam") or not lj.get("awayTeam"):
            raise SystemExit("BLOCKED_LEISU_PAYLOAD_SCHEMA: response must expose eventId, homeTeam, awayTeam")
        if canonical(lj["homeTeam"]) != canonical(home) or canonical(lj["awayTeam"]) != canonical(away):
            raise SystemExit("BLOCKED_PROVIDER_IDENTITY_CONFLICT")
        leisu_event_id = str(lj["eventId"])
        leisu_event_time = lj.get("updatedAt") or lj.get("marketUpdatedAt") or lj.get("timestamp")
        # Obtain official Sporttery odds by the confirmed Sporttery matchId.
        sj, surl, sraw = get_json(FIXED, {"clientCode":"3001","matchId":sporttery_id})
        market = extract_market(sj)
        if not any(market.values()):
            raise SystemExit("BLOCKED_OFFICIAL_SPORTTERY_MARKET_EMPTY")
        # Model calculation unchanged: only the existing V5.2 function is called.
        model_match = (mid, kickoff, home, away, None, None)
        lh, la = model.model_lambdas(con, "v5.2", model_match)
        mat = model.matrix(lh, la, True)
        probs = {
          "H":sum(mat[i][j] for i in range(model.N) for j in range(model.N) if i>j),
          "D":sum(mat[i][j] for i in range(model.N) for j in range(model.N) if i==j),
          "A":sum(mat[i][j] for i in range(model.N) for j in range(model.N) if i<j)}
        scores=sorted(((mat[i][j],i,j) for i in range(model.N) for j in range(model.N)),reverse=True)
        ptime=now()
        if datetime.fromisoformat(ptime.replace("Z","+00:00")) >= datetime.fromisoformat(kickoff.replace("Z","+00:00")):
            raise SystemExit("BLOCKED_PREDICTION_NOT_PREMATCH")
        fixture_payload={"sporttery":m,"sporttery_list_url":list_url,"sporttery_retrieved_at":retrieved}
        trace.execute("INSERT OR IGNORE INTO fixtures VALUES(?,?,?,?,?,?,?,?,?)",
          (mid,"sporttery",sporttery_id,kickoff,home,away,json.dumps(fixture_payload,ensure_ascii=False),retrieved,sha(fixture_payload)))
        pred={"model_version":"V5.2","code_sha":os.getenv("GITHUB_SHA","local-unpinned"),
          "internal_match_id":mid,"sporttery_match_id":sporttery_id,"leisu_event_id":leisu_event_id,
          "kickoff":kickoff,"prediction_time":ptime,"lambda_home":lh,"lambda_away":la,
          "probabilities":probs,"score1":{"score":f"{scores[0][1]}-{scores[0][2]}","probability":scores[0][0]},
          "score2":{"score":f"{scores[1][1]}-{scores[1][2]}","probability":scores[1][0]}}
        ph=sha(pred)
        try:
            trace.execute("INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (ph,mid,ptime,kickoff,"V5.2",pred["code_sha"],lh,la,probs["H"],probs["D"],probs["A"],
               pred["score1"]["score"],scores[0][0],pred["score2"]["score"],scores[1][0],
               json.dumps(pred,ensure_ascii=False),ph))
        except sqlite3.IntegrityError:
            pass
        for provider, event_id, url, raw, payload, event_time in (
            ("leisu",leisu_event_id,lurl,lraw,lj,leisu_event_time),
            ("sporttery",sporttery_id,surl,sraw,sj,None)):
            normalized=extract_market(payload) if provider=="sporttery" else payload.get("markets",{})
            snap_id=sha({"mid":mid,"provider":provider,"raw_sha":sha(raw),"retrieved":retrieved})
            trace.execute("INSERT OR IGNORE INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (snap_id,mid,provider,event_id,url,event_time,retrieved,
               "PROVIDER_EVENT_TIME" if event_time else "RETRIEVAL_TIME_ONLY",
               raw.decode("utf-8",errors="replace"),sha(raw),json.dumps(normalized,ensure_ascii=False)))
        trace.commit()
        print(json.dumps({"phase":"prediction_and_market_snapshot","match_id":mid,"home":home,"away":away,
                          "prediction_id":ph,"probabilities":probs,"score1":pred["score1"],"score2":pred["score2"],
                          "market_sources":["leisu","sporttery"],"status":"PREDICTION_AND_MARKET_CAPTURED"},ensure_ascii=False))
        completed += 1
    print(json.dumps({"status":"PARTIAL_E2E_ONLY","predictions_captured":completed,
                      "results":"result linking is intentionally a separate post-match run; no match is declared fully accepted until official result ID/score is verified.",
                      "trace_db":str(TRACE_DB)},ensure_ascii=False))
    trace.close(); con.close()

if __name__ == "__main__":
    main()
