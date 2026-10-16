# Prediction Traceability Contract

## Purpose
Establish an auditable chain from exact pre-match inputs to frozen model output and eventual result. This is a data-lineage change only: it must not alter V5.1/V5.2 prediction logic or overwrite historical predictions.

## Non-negotiable rules
1. Every fixture has a stable internal match_id; provider IDs are stored separately with provider namespace (Sporttery, Leisu, etc.).
2. Every prediction run gets a unique prediction_id and immutable prediction_timestamp_utc plus prediction_timestamp_beijing.
3. Save exact model version, Git commit SHA, config/parameter hash, and feature-data cutoff used for the prediction.
4. Store all three 90-minute probabilities (home/draw/away) and lambda_home / lambda_away as emitted by the model. Never reconstruct these after kickoff and label them as original outputs.
5. Store score1 and score2 separately. Handicap result is calculated mechanically from score1 only; score2 never participates in handicap settlement.
6. Every market record must contain the source event/effective timestamp (when supplied) and our retrieval timestamp. Retrieval timestamp alone does not prove odds were available at that time.
7. Store Sporttery handicap line and official handicap 1X2 SP values separately from Asian handicap and Asian totals. Never substitute Asian totals for official Sporttery total-goals data.
8. Preserve raw provider payloads (or immutable raw-file references and SHA-256 hashes) alongside normalized fields.
9. Append-only: do not edit old prediction snapshots after kickoff. Corrections become a new versioned audit record with reason and source.
10. Missing, ambiguous, stale, or untimestamped data must remain explicitly missing/flagged; do not infer it from post-match odds or another fixture.

## Required records

### A. Fixture identity (fixtures)
- match_id (internal stable ID)
- competition, season, match_date_local, kickoff_utc, kickoff_beijing
- home_team_id, home_team_name, away_team_id, away_team_name
- provider_match_ids_json (provider namespace + ID)
- identity_status: VERIFIED, AMBIGUOUS, UNRESOLVED
- identity_evidence_ref

### B. Frozen model prediction (prediction_snapshots)
- prediction_id, match_id, prediction_timestamp_utc, prediction_timestamp_beijing
- scheduled_kickoff_utc, lead_minutes, prediction_window_status (e.g. T_MINUS_12H, OUT_OF_WINDOW)
- model_name, model_version, git_commit_sha, config_sha256, feature_snapshot_id
- home_win_prob, draw_prob, away_win_prob (0-1; sum must be approximately 1)
- lambda_home, lambda_away
- score1_home_goals, score1_away_goals, score2_home_goals, score2_away_goals
- predicted_ht_ft, predicted_90min_result, predicted_handicap_result
- sporttery_handicap_line, risk_flags_json
- raw_prediction_ref, raw_prediction_sha256

### C. Market snapshot (market_snapshots)
- market_snapshot_id, match_id, prediction_id (nullable if snapshot collected outside prediction run)
- provider, market_type, source_url
- provider_event_timestamp_utc (nullable if provider does not supply it)
- retrieved_at_utc, retrieved_at_beijing
- home_odds, draw_odds, away_odds
- sporttery_handicap_line, sporttery_handicap_home_sp, sporttery_handicap_draw_sp, sporttery_handicap_away_sp
- asian_handicap_line, asian_home_odds, asian_away_odds
- asian_total_line, over_odds, under_odds
- official_sporttery_total_goals_market_json (nullable; no substitution)
- raw_payload_ref, raw_payload_sha256
- timestamp_quality: PROVIDER_TIMESTAMPED, RETRIEVAL_TIME_ONLY, UNTIMESTAMPED
- quality_status: VALID_PREMATCH, AFTER_KICKOFF, STALE, MISSING_FIELDS, AMBIGUOUS_FIXTURE

### D. Input/features snapshot (feature_snapshots)
- feature_snapshot_id, match_id, captured_at_utc, feature_cutoff_utc
- team_form_ref, team_xg_last_5_json, lineup_status, injury_status
- schedule_fatigue_json, weather_ref, referee_ref, news_refs_json
- source_coverage_json, raw_payload_refs_json, payload_hashes_json
- Unknown values remain null with explicit missingness/quality flags.

### E. Result and review (results, reviews)
- match_id, result_source, result_source_match_id, result_retrieved_at_utc
- home_goals_90, away_goals_90, result_status, verification_status
- result_payload_ref, result_payload_sha256
- Evaluation joins to a specific immutable prediction_id; no silent replacement with a later prediction.
- Keep 90-minute H/D/A, exact-score, score1/score2, handicap H/D/A, and totals metrics separate.

## Validation gates
Reject or quarantine a prediction snapshot if:
- fixture identity is not unique;
- probability fields are missing, outside [0,1], or do not sum to approximately 1;
- lambda fields are missing or non-numeric;
- model version / commit / config hash is missing;
- prediction timestamp is at or after kickoff for a pre-match prediction;
- a market snapshot is used as pre-match evidence without a defensible timestamp;
- handicap result cannot be reproduced from score1 and the recorded Sporttery line.

## First implementation sequence
1. Freeze this contract and audit existing prediction/market files against it.
2. Start writing immutable prediction snapshots at generation time (probabilities, lambdas, model commit/config, exact timestamp).
3. Capture Sporttery and Leisu raw market payloads with event/retrieval timestamps and hashes; store official handicap and totals separately.
4. Join by verified fixture identity, never by team names alone when the match is ambiguous.
5. Run a small end-to-end acceptance test before expanding collection.
6. Only after traceability coverage is measured should any model calibration experiment be considered.

## Current known limitation
The 2026-10-16 review CSV explicitly has blank historical probability and lambda fields because they were not retained in the original displayed prediction. Those values must remain marked unavailable; they must not be reverse-engineered and presented as historical model outputs.
