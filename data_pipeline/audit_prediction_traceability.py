#!/usr/bin/env python3
"""Read-only audit of archived prediction and market records; never edits source files."""
import csv, json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
PRED = ROOT / "reviews/2026-10-16-prediction-results-and-market-audit.csv"
MARKET = ROOT / "reviews/2026-10-16-prematch-market-snapshots.csv"
OUT = ROOT / "reviews/traceability-audit-2026-10-16.json"

def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def blank(v):
    return v is None or not str(v).strip()

def coverage(rs, fields):
    out = {}
    n = len(rs)
    for field in fields:
        present = sum(not blank(r.get(field)) for r in rs)
        out[field] = {"present": present, "missing": n-present,
                      "missing_rate": round((n-present)/n, 4) if n else None}
    return out

def main():
    preds = rows(PRED)
    markets = rows(MARKET)
    pred_fields = [
        "home_win_prob", "draw_prob", "away_win_prob",
        "lambda_home", "lambda_away", "kickoff_beijing",
        "prediction_version", "prediction_source",
    ]
    market_fields = [
        "snapshot_timestamp_beijing", "source", "source_url",
        "odds_home", "odds_draw", "odds_away", "sporttery_handicap",
        "sporttery_handicap_home_sp", "sporttery_handicap_draw_sp",
        "sporttery_handicap_away_sp", "ou_line", "over_odds", "under_odds",
    ]
    pred_ids = [r.get("match_id","").strip() for r in preds]
    # The existing review CSV does not have a match_id column; don't infer a provider ID from names.
    id_present = sum(bool(x) for x in pred_ids)
    probs_complete = sum(all(not blank(r.get(k)) for k in ("home_win_prob","draw_prob","away_win_prob")) for r in preds)
    lambdas_complete = sum(all(not blank(r.get(k)) for k in ("lambda_home","lambda_away")) for r in preds)
    probs_valid = 0
    for r in preds:
        try:
            p = [float(r[k]) for k in ("home_win_prob","draw_prob","away_win_prob")]
            if all(0 <= x <= 1 for x in p) and abs(sum(p)-1) <= 0.01:
                probs_valid += 1
        except (ValueError, TypeError, KeyError):
            pass
    market_timestamped = sum(not blank(r.get("snapshot_timestamp_beijing")) for r in markets)
    market_valid_source = sum(not blank(r.get("source")) and not blank(r.get("source_url")) for r in markets)
    report = {
        "audit_created_note": "Read-only audit; historical missing values are not reconstructed.",
        "prediction_file": str(PRED.relative_to(ROOT)),
        "prediction_rows": len(preds),
        "prediction_field_coverage": coverage(preds, pred_fields),
        "prediction_match_id": {"present": id_present, "missing": len(preds)-id_present,
                                "missing_rate": round((len(preds)-id_present)/len(preds),4) if preds else None},
        "complete_probability_triplets": probs_complete,
        "valid_probability_triplets": probs_valid,
        "complete_lambda_pairs": lambdas_complete,
        "market_file": str(MARKET.relative_to(ROOT)),
        "market_rows": len(markets),
        "market_field_coverage": coverage(markets, market_fields),
        "market_timestamped_rows": market_timestamped,
        "market_timestamp_missing_rows": len(markets)-market_timestamped,
        "market_rows_with_source_and_url": market_valid_source,
        "joinability": {
            "prediction_to_market_by_unique_match_id": "BLOCKED: prediction archive has no match_id and market archive has no provider event ID",
            "prediction_to_result_by_unique_match_id": "BLOCKED: prediction archive has no match_id",
            "note": "Team names and kickoff labels are not accepted as a unique-ID substitute without a verified fixture mapping."
        },
        "overall_status": "FAIL_TRACEABILITY_GAPS",
        "next_actions": [
            "Write match_id and provider event IDs at fixture-ingestion time.",
            "At prediction generation, append probabilities, lambdas, exact timestamp, model version, git SHA and config hash.",
            "Capture source event time separately from retrieval time for every market snapshot.",
            "Preserve raw source payload and SHA-256; do not overwrite historical snapshots."
        ]
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
