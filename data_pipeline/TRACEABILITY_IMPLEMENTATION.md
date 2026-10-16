# Traceability rollout: current state and acceptance gates

## 1. Existing archive audit

Run:

```bash
python data_pipeline/audit_prediction_traceability.py
```

It reads the 2026-10-16 prediction and market CSVs and writes `reviews/traceability-audit-2026-10-16.json`. The audit is read-only and does not fill missing historical fields.

Current known issues from the checked-in files:
- The 14-row prediction review CSV contains no stable `match_id`.
- The 14 rows have no retained home/draw/away probabilities or `lambda_home/lambda_away`.
- The file has no prediction-generation timestamp field. A result-review date is not a prediction timestamp.
- `prediction_version` says `临时展示预测`, not an exact model version/commit/config.
- The separate 14-row market file has 7 rows with a timestamp and 7 without. The timestamped rows identify a third-party public match page with cross-check notes; they are not raw Leisu API payloads.
- No unique ID join from the current prediction archive to market and result archive is possible until a verified fixture-ID mapping is added.
- Historical probabilities and lambdas are irrecoverable unless the original model run logs/artifacts exist. Do not backfill them from final scores or closing odds.

## 2. Append-only persistence

`prediction_snapshot_writer.py` provides a new, separate SQLite trace store with:
- required probability, lambda, model version, commit SHA, config hash, timestamps and fixture ID;
- payload SHA-256;
- database triggers that reject UPDATE and DELETE for prediction and market trace rows;
- raw market payload plus normalized fields;
- explicit provider event timestamp and retrieval timestamp.

This is a storage component, not a model runner. The production prediction entrypoint must call `append_prediction()` immediately after producing a prediction and call `append_market()` when a source response is received. Until that caller is wired and tested, automatic capture is not considered active.

## 3. Timestamp policy

- `provider_event_timestamp_utc`: provider's own event/market update time, if provided.
- `retrieved_at_utc`: our actual collection time.
- If only retrieval time exists, set `timestamp_quality=RETRIEVAL_TIME_ONLY`; never call it the bookmaker update time.
- If no defensible time exists, set `timestamp_quality=UNTIMESTAMPED`.
- Only snapshots proven to precede kickoff qualify as pre-match evidence.

## 4. Provider priority

1. Leisu: first choice for fixture/result cross-check and market history where accessible; preserve original response, event ID, response URL, retrieval timestamp and hash.
2. Sporttery official API: authoritative for Sporttery match ID, official handicap pool (`hhad`) and official total-goals market where available.
3. Secondary pages can be cross-checks only, explicitly labeled as such. Do not mislabel third-party snapshots as raw Leisu or official Sporttery source records.

## 5. End-to-end acceptance (small batch)

Do not expand until each test fixture passes:
- exactly one internal match_id and one verified provider event ID per provider;
- prediction timestamp strictly before kickoff and model metadata complete;
- probability triplet valid and sums to 1±0.01;
- both lambdas present and finite;
- at least one market record stores source, retrieval timestamp, timestamp quality and raw-payload hash;
- if provider supplies event time, preserve it separately;
- market snapshot is pre-kickoff; after-kickoff data is flagged and excluded;
- result source ID maps to the same fixture and verified 90-minute score;
- no UPDATE/DELETE can mutate the original prediction/market trace;
- no production model file/version is changed during this work.

## 6. Current status
The audit script and append-only writer are infrastructure only. The first audit report must be run in CI or a runtime containing the archived CSVs. The model-generation entrypoint and live provider collectors still need explicit integration and a real end-to-end run before the pipeline can be called operational.
