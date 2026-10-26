"""Audit whether the repository SQLite DB contains usable historical pre-kickoff market snapshots."""
import json, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"football_model_database.sqlite"
OUT=ROOT/"model_validation/market_lambda_source_audit.json"
REQUIRED=("match_id","captured_at","euro_home_odds","euro_draw_odds","euro_away_odds","ou_line","over_odds","under_odds")

def dt(value):
    if not value: return None
    try:
        x=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def main():
    report={"database":str(DB.name),"status":"BLOCKED","market_table_present":False,
            "market_columns":[],"matches_with_final_score":0,"market_rows":0,
            "complete_1x2_ou_rows":0,"valid_pre_kickoff_6h_rows":0,
            "valid_pre_kickoff_12h_rows":0,"distinct_fixtures_6h":0,
            "distinct_fixtures_12h":0,"provider_timestamp_column_present":False,
            "source_column_present":False,"payload_hash_column_present":False,
            "blockers":[]}
    if not DB.exists():
        report["blockers"].append("SQLite database file is missing.")
    else:
        con=sqlite3.connect(DB)
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "market_snapshot" not in tables:
            report["blockers"].append("market_snapshot table is absent.")
        else:
            report["market_table_present"]=True
            cols=[r[1] for r in con.execute("PRAGMA table_info(market_snapshot)")]
            report["market_columns"]=cols
            cset=set(cols)
            report["provider_timestamp_column_present"]=bool({"provider_timestamp","provider_event_timestamp","event_timestamp"} & cset)
            report["source_column_present"]=bool({"source","provider","bookmaker","source_name"} & cset)
            report["payload_hash_column_present"]=bool({"payload_sha256","raw_payload_sha256","payload_hash"} & cset)
            missing=[c for c in REQUIRED if c not in cset]
            if missing:
                report["blockers"].append("Missing required columns: "+", ".join(missing))
            else:
                count=con.execute("SELECT COUNT(*) FROM market_snapshot").fetchone()[0]
                report["market_rows"]=count
                matches={r[0]:r[1] for r in con.execute("SELECT match_id,kickoff FROM matches")}
                scored={r[0] for r in con.execute("SELECT match_id FROM results WHERE ft_home IS NOT NULL AND ft_away IS NOT NULL")}
                report["matches_with_final_score"]=len(scored)
                eligible6=set(); eligible12=set()
                for row in con.execute("SELECT match_id,captured_at,euro_home_odds,euro_draw_odds,euro_away_odds,ou_line,over_odds,under_odds FROM market_snapshot"):
                    mid,cap,*vals=row
                    if not all(v is not None and str(v).strip() for v in vals): continue
                    try:
                        if any(float(v)<=1 for v in vals if v is not vals[3]): pass
                        [float(v) for v in vals]
                    except Exception:
                        continue
                    report["complete_1x2_ou_rows"]+=1
                    kickoff=dt(matches.get(mid))
                    captured=dt(cap)
                    if kickoff is None or captured is None or mid not in scored or captured>=kickoff: continue
                    gap=kickoff-captured
                    if gap>=timedelta(hours=6): eligible6.add(mid)
                    if gap>=timedelta(hours=12): eligible12.add(mid)
                report["valid_pre_kickoff_6h_rows"]=len(eligible6)
                report["valid_pre_kickoff_12h_rows"]=len(eligible12)
                report["distinct_fixtures_6h"]=len(eligible6)
                report["distinct_fixtures_12h"]=len(eligible12)
                if not report["provider_timestamp_column_present"]:
                    report["blockers"].append("No explicit provider event timestamp column; captured_at alone is not proof of bookmaker update time.")
                if not report["source_column_present"]:
                    report["blockers"].append("No market source/provider column.")
                if not report["payload_hash_column_present"]:
                    report["blockers"].append("No retained raw payload SHA-256 column.")
                if len(eligible12)<10:
                    report["blockers"].append("Fewer than 10 unique completed fixtures have complete 1X2+O/U snapshots at least 12 hours pre-kickoff.")
                if not report["blockers"]: report["status"]="READY_FOR_CHRONOLOGICAL_BACKTEST"
        con.close()
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
