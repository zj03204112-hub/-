"""Append-only per-match xG observations and reproducible ISO-week snapshots.

This module never fetches or fabricates xG. Callers must pass source observations or
explicit missing records. Provider measurements and model estimates are separate groups.
"""
import hashlib
import json
import math
import sqlite3
import uuid
from datetime import date, datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS match_xg_observation (
  xg_record_id TEXT PRIMARY KEY,
  match_id TEXT NOT NULL,
  provider_event_id TEXT,
  team_id TEXT NOT NULL,
  competition_id TEXT NOT NULL,
  season_id TEXT NOT NULL,
  match_date TEXT NOT NULL,
  iso_year INTEGER NOT NULL,
  iso_week INTEGER NOT NULL,
  home_away TEXT NOT NULL CHECK(home_away IN ('home','away')),
  provider TEXT NOT NULL,
  provider_version TEXT NOT NULL,
  source_url TEXT,
  captured_at_utc TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK(value_type IN ('MEASURED','MODEL_ESTIMATE','MISSING')),
  xg_for REAL,
  xg_against REAL,
  xgd REAL,
  missing_reason TEXT,
  raw_payload_sha256 TEXT,
  record_sha256 TEXT NOT NULL,
  CHECK ((value_type='MISSING' AND xg_for IS NULL AND xg_against IS NULL
          AND xgd IS NULL AND missing_reason IS NOT NULL)
      OR (value_type IN ('MEASURED','MODEL_ESTIMATE') AND xg_for IS NOT NULL
          AND xg_against IS NOT NULL AND xgd IS NOT NULL AND missing_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_xg_week ON match_xg_observation
  (team_id, competition_id, season_id, iso_year, iso_week, provider, provider_version, value_type);
CREATE TRIGGER IF NOT EXISTS match_xg_no_update
BEFORE UPDATE ON match_xg_observation BEGIN
  SELECT RAISE(ABORT, 'xG observations are append-only; append a correction record');
END;
CREATE TRIGGER IF NOT EXISTS match_xg_no_delete
BEFORE DELETE ON match_xg_observation BEGIN
  SELECT RAISE(ABORT, 'xG observations are append-only; delete forbidden');
END;

CREATE TABLE IF NOT EXISTS team_weekly_xg_snapshot (
  summary_id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL,
  competition_id TEXT NOT NULL,
  season_id TEXT NOT NULL,
  iso_year INTEGER NOT NULL,
  iso_week INTEGER NOT NULL,
  provider TEXT NOT NULL,
  provider_version TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK(value_type IN ('MEASURED','MODEL_ESTIMATE','MISSING')),
  week_start TEXT NOT NULL,
  week_end TEXT NOT NULL,
  matches_count INTEGER NOT NULL,
  xg_covered_matches INTEGER NOT NULL,
  week_xg REAL,
  week_xga REAL,
  week_xgd REAL,
  avg_xg_per90 REAL,
  avg_xga_per90 REAL,
  avg_xgd_per90 REAL,
  coverage REAL NOT NULL,
  source_record_digest TEXT NOT NULL,
  created_at_utc TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS team_weekly_xg_no_update
BEFORE UPDATE ON team_weekly_xg_snapshot BEGIN
  SELECT RAISE(ABORT, 'weekly xG snapshots are append-only; append a new snapshot');
END;
CREATE TRIGGER IF NOT EXISTS team_weekly_xg_no_delete
BEFORE DELETE ON team_weekly_xg_snapshot BEGIN
  SELECT RAISE(ABORT, 'weekly xG snapshots are append-only; delete forbidden');
END;
"""

def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def init_db(db_path):
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    con.commit()
    return con

def append_match_xg(con, record):
    required = ('match_id','team_id','competition_id','season_id','match_date',
                'home_away','provider','provider_version','captured_at_utc','value_type')
    missing = [key for key in required if record.get(key) in (None, '')]
    if missing:
        raise ValueError('missing xG record fields: ' + ', '.join(missing))
    try:
        match_day = date.fromisoformat(str(record['match_date'])[:10])
        captured = datetime.fromisoformat(str(record['captured_at_utc']).replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('match_date must be YYYY-MM-DD and captured_at_utc ISO-8601') from exc
    if captured.tzinfo is None:
        raise ValueError('captured_at_utc must include timezone')
    if record['home_away'] not in ('home','away'):
        raise ValueError('home_away must be home or away')
    value_type = record['value_type']
    if value_type not in ('MEASURED','MODEL_ESTIMATE','MISSING'):
        raise ValueError('value_type must be MEASURED, MODEL_ESTIMATE, or MISSING')
    if value_type == 'MISSING':
        if not record.get('missing_reason'):
            raise ValueError('missing_reason is required for MISSING xG records')
        xgf = xga = xgd = None
    else:
        if record.get('missing_reason'):
            raise ValueError('measured/estimated values cannot carry a missing_reason')
        try:
            xgf, xga = float(record['xg_for']), float(record['xg_against'])
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError('xg_for and xg_against are required numeric values') from exc
        if not all(math.isfinite(v) and v >= 0 for v in (xgf, xga)):
            raise ValueError('xG values must be finite and non-negative')
        xgd = xgf - xga
    iso = match_day.isocalendar()
    base = dict(record)
    base.update({'iso_year': iso.year, 'iso_week': iso.week, 'xg_for': xgf,
                 'xg_against': xga, 'xgd': xgd})
    record_json = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    record_sha = _sha(record_json)
    record_id = record.get('xg_record_id') or str(uuid.uuid5(uuid.NAMESPACE_URL, record_sha))
    con.execute('''INSERT INTO match_xg_observation
      (xg_record_id,match_id,provider_event_id,team_id,competition_id,season_id,
       match_date,iso_year,iso_week,home_away,provider,provider_version,source_url,
       captured_at_utc,value_type,xg_for,xg_against,xgd,missing_reason,
       raw_payload_sha256,record_sha256)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (record_id,record['match_id'],record.get('provider_event_id'),record['team_id'],
       record['competition_id'],record['season_id'],match_day.isoformat(),iso.year,iso.week,
       record['home_away'],record['provider'],record['provider_version'],record.get('source_url'),
       captured.isoformat(),value_type,xgf,xga,xgd,record.get('missing_reason'),
       record.get('raw_payload_sha256'),record_sha))
    con.commit()
    return record_id, record_sha

def append_weekly_summary(con, team_id, competition_id, season_id, iso_year, iso_week,
                          provider, provider_version, value_type):
    """Append a deterministic snapshot derived only from matching immutable observations."""
    if value_type not in ('MEASURED','MODEL_ESTIMATE','MISSING'):
        raise ValueError('invalid value_type')
    rows = con.execute('''SELECT xg_record_id,match_id,match_date,xg_for,xg_against,xgd,
                                  value_type,record_sha256
                           FROM match_xg_observation
                           WHERE team_id=? AND competition_id=? AND season_id=?
                             AND iso_year=? AND iso_week=? AND provider=?
                             AND provider_version=? AND value_type=?
                           ORDER BY match_date,match_id,xg_record_id''',
                       (team_id,competition_id,season_id,iso_year,iso_week,
                        provider,provider_version,value_type)).fetchall()
    if not rows:
        raise ValueError('no xG observations for requested team/week/source group')
    record_digest = _sha(json.dumps([list(r) for r in rows], ensure_ascii=False, separators=(',',':')))
    dates = [date.fromisoformat(r[2]) for r in rows]
    week_start = date.fromisocalendar(int(iso_year), int(iso_week), 1)
    week_end = date.fromisocalendar(int(iso_year), int(iso_week), 7)
    valid = [r for r in rows if r[3] is not None and r[4] is not None]
    matches_count = len(set(r[1] for r in rows))
    covered_matches = len(set(r[1] for r in valid))
    if valid:
        sxg = sum(r[3] for r in valid)
        sxga = sum(r[4] for r in valid)
        sxgd = sum(r[5] for r in valid)
        avgxg, avgxga, avgxgd = sxg/covered_matches, sxga/covered_matches, sxgd/covered_matches
    else:
        sxg = sxga = sxgd = avgxg = avgxga = avgxgd = None
    coverage = covered_matches / matches_count if matches_count else 0.0
    snapshot = {
      'team_id':team_id,'competition_id':competition_id,'season_id':season_id,
      'iso_year':int(iso_year),'iso_week':int(iso_week),'provider':provider,
      'provider_version':provider_version,'value_type':value_type,
      'week_start':week_start.isoformat(),'week_end':week_end.isoformat(),
      'matches_count':matches_count,'xg_covered_matches':covered_matches,
      'week_xg':sxg,'week_xga':sxga,'week_xgd':sxgd,
      'avg_xg_per90':avgxg,'avg_xga_per90':avgxga,'avg_xgd_per90':avgxd,
      'coverage':coverage,'source_record_digest':record_digest
    }
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    snapshot_sha = _sha(snapshot_json)
    summary_id = str(uuid.uuid5(uuid.NAMESPACE_URL, snapshot_sha))
    con.execute('''INSERT OR IGNORE INTO team_weekly_xg_snapshot
      (summary_id,team_id,competition_id,season_id,iso_year,iso_week,provider,provider_version,
       value_type,week_start,week_end,matches_count,xg_covered_matches,week_xg,week_xga,week_xgd,
       avg_xg_per90,avg_xga_per90,avg_xgd_per90,coverage,source_record_digest,created_at_utc,snapshot_sha256)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (summary_id,team_id,competition_id,season_id,int(iso_year),int(iso_week),provider,
       provider_version,value_type,week_start.isoformat(),week_end.isoformat(),matches_count,
       covered_matches,sxg,sxga,sxgd,avgxg,avgxga,avgxgd,coverage,record_digest,_utc_now(),snapshot_sha))
    con.commit()
    return snapshot

def audit_weekly_coverage(con):
    """Return coverage and recomputation checks; missing observations are not imputed."""
    rows = con.execute('''SELECT team_id,competition_id,season_id,iso_year,iso_week,provider,
                                 provider_version,value_type,COUNT(DISTINCT match_id),
                                 COUNT(DISTINCT CASE WHEN xg_for IS NOT NULL AND xg_against IS NOT NULL THEN match_id END)
                          FROM match_xg_observation
                          GROUP BY team_id,competition_id,season_id,iso_year,iso_week,provider,
                                   provider_version,value_type
                          ORDER BY competition_id,season_id,iso_year,iso_week,team_id,provider''').fetchall()
    out = []
    for r in rows:
        out.append({
          'team_id':r[0],'competition_id':r[1],'season_id':r[2],'iso_year':r[3],'iso_week':r[4],
          'provider':r[5],'provider_version':r[6],'value_type':r[7],
          'matches_count':r[8],'xg_covered_matches':r[9],
          'coverage':round(r[9]/r[8],4) if r[8] else 0.0,
          'weekly_snapshot_exists':con.execute('''SELECT 1 FROM team_weekly_xg_snapshot
             WHERE team_id=? AND competition_id=? AND season_id=? AND iso_year=? AND iso_week=?
               AND provider=? AND provider_version=? AND value_type=? LIMIT 1''',
             (r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7])).fetchone() is not None
        })
    return out
