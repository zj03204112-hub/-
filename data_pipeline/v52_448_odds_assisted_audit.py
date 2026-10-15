#!/usr/bin/env python3
"""Leakage-safe odds-assisted 448-match audit. Isolated experiment only."""
import csv, json, math, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, confusion_matrix

ROOT = Path(__file__).resolve().parents[1]
CSV_IN = ROOT / "model_validation/v52_jincai_20260901_20261013/v52_plus_nations_league_448_merged_backtest_20261013.csv"
DB = ROOT / "football_model_database.sqlite"
OUT = ROOT / "model_validation/v52_jincai_20260901_20261013/v52_448_odds_assisted_audit.json"
CUTOFF_HOURS = 12
CLASSES = ["H", "D", "A"]

def dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception: return None

def main():
    with CSV_IN.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 448:
        raise SystemExit(f"FROZEN_SAMPLE_FAIL rows={len(rows)}")
    con = sqlite3.connect(DB)
    tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "sporttery_market" not in tables or "matches" not in tables:
        raise SystemExit("ODDS_SCHEMA_MISSING")
    cols = {x[1] for x in con.execute("PRAGMA table_info(sporttery_market)")}
    required = {"match_id","pool_code","handicap","home_value","draw_value","away_value","captured_at"}
    if not required.issubset(cols):
        raise SystemExit(f"ODDS_COLUMNS_MISSING {sorted(required-cols)}")
    # Only observations captured at least 12 hours before kickoff are eligible.
    market = con.execute("""
      SELECT sm.match_id, sm.pool_code, sm.handicap, sm.home_value, sm.draw_value,
             sm.away_value, sm.captured_at, m.kickoff
      FROM sporttery_market sm JOIN matches m ON m.match_id=sm.match_id
      WHERE sm.pool_code IN ('hhad','asian_handicap_avg')
    """).fetchall()
    con.close()
    best = {}
    for mid, pool, line, hv, dv, av, captured, kickoff in market:
        k, c = dt(kickoff), dt(captured)
        if not k or not c or c > k - timedelta(hours=CUTOFF_HOURS): continue
        try:
            line = float(line)
            hv, dv, av = float(hv), float(dv), float(av)
            if not all(math.isfinite(v) and v > 1.0 for v in (hv,dv,av)): continue
        except (TypeError, ValueError): continue
        key = str(mid)
        # Prefer official Sporttery handicap odds over generic Asian line, then latest eligible capture.
        rank = (1 if pool == "hhad" else 0, c)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, [line, hv, dv, av, 1/hv, 1/dv, 1/av, (1/hv)/(1/hv+1/dv+1/av), (1/dv)/(1/hv+1/dv+1/av), (1/av)/(1/hv+1/dv+1/av)], pool, captured)
    enriched = []
    missing_reason = Counter()
    for r in rows:
        mid = str(r.get("match_id",""))
        if mid not in best:
            missing_reason[r.get("sample_source","UNKNOWN")] += 1
            continue
        try:
            p = [float(r["p_home"]), float(r["p_draw"]), float(r["p_away"])]
            if not all(math.isfinite(x) and x >= 0 for x in p) or sum(p) <= 0: continue
            p = [x/sum(p) for x in p]
            y = r["actual_result"]
            if y not in CLASSES: continue
            market_features = best[mid][1]
            enriched.append({"row":r,"p":p,"y":y,"features":p+market_features,"pool":best[mid][2],"captured_at":best[mid][3]})
        except (ValueError, KeyError, TypeError): continue
    report = {
      "title":"448-match odds-assisted prediction audit",
      "sample_rows":len(rows), "eligible_matches_with_verified_T_minus_12h_odds":len(enriched),
      "missing_odds_by_source":dict(missing_reason), "cutoff_hours_before_kickoff":CUTOFF_HOURS,
      "market_sources_used":["hhad","asian_handicap_avg"],
      "guardrails":["Only market observations captured no later than kickoff minus 12 hours are eligible.",
                    "Chronological 70/30 split; odds-assisted classifier trains only on earlier matches.",
                    "Baseline and odds-assisted model are scored on the exact same eligible holdout matches.",
                    "This is an isolated experiment; no production model or stored prediction is changed."]
    }
    if len(enriched) < 80:
        report["status"] = "INSUFFICIENT_VERIFIED_PREMATCH_ODDS"
        report["note"] = "Not enough verified pre-match odds rows for a reliable train/holdout comparison; no accuracy improvement claim is made."
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return
    enriched.sort(key=lambda x:(x["row"].get("kickoff",""),x["row"].get("match_id","")))
    split=int(len(enriched)*0.70)
    train,test=enriched[:split],enriched[split:]
    Xtr=np.array([x["features"] for x in train],dtype=float)
    Xte=np.array([x["features"] for x in test],dtype=float)
    ytr=np.array([CLASSES.index(x["y"]) for x in train])
    yte=np.array([CLASSES.index(x["y"]) for x in test])
    model=make_pipeline(SimpleImputer(strategy="median"),StandardScaler(),LogisticRegression(max_iter=3000,C=0.5,class_weight=None))
    model.fit(Xtr,ytr)
    probs=np.zeros((len(test),3))
    raw=model.predict_proba(Xte)
    for j,c in enumerate(model[-1].classes_): probs[:,int(c)]=raw[:,j]
    base=np.array([x["p"] for x in test])
    yhat=probs.argmax(axis=1); basehat=base.argmax(axis=1)
    def m(p):
        pred=p.argmax(axis=1)
        cm=confusion_matrix(yte,pred,labels=[0,1,2])
        return {"n":len(yte),"correct":int((pred==yte).sum()),"accuracy":round(float(accuracy_score(yte,pred)),6),
                "log_loss":round(float(log_loss(yte,p,labels=[0,1,2])),6),
                "brier":round(float(np.mean(np.sum((p-np.eye(3)[yte])**2,axis=1))),6),
                "draw_recall":round(float(cm[1,1]/cm[1].sum()),6) if cm[1].sum() else None,
                "confusion_actual_rows_predicted_columns":cm.tolist()}
    report.update({"status":"COMPLETED","split":{"train":len(train),"holdout":len(test)},
      "baseline_v5_probabilities":m(base),"odds_assisted_classifier":m(probs),
      "accuracy_delta":round(float(accuracy_score(yte,yhat)-accuracy_score(yte,basehat)),6),
      "matched_holdout_sources":dict(Counter(x["row"].get("sample_source","UNKNOWN") for x in test)),
      "caution":"Odds-assisted classifier uses historical handicap-market features; any gain must be replicated on a separate forward blind sample before production adoption."})
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
