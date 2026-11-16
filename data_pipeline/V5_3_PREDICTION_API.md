# V5.3 callable prediction interface

## Entry point

The callable interface is:

```python
from data_pipeline.v5_3_prediction_api import predict_and_archive

result = predict_and_archive(
    db_path="data/v5_3_prediction_trace.sqlite",
    match_id="YOUR_VERIFIED_INTERNAL_MATCH_ID",
    prediction_id="YOUR_UNIQUE_IMMUTABLE_PREDICTION_ID",
    git_commit_sha="THE_40_CHARACTER_GIT_COMMIT_SHA",
    home_team="Home FC",
    away_team="Away FC",
    kickoff_iso="2026-11-20T19:00:00+08:00",
    snapshot_time_iso="2026-11-20T08:00:00+08:00",
    odds_provider="ACTUAL_PROVIDER_NAME",
    odds_source_url="ACTUAL_SOURCE_PAGE_URL",
    odds_verified=True,
    goal_line=2.5,
    over_odds=1.90,
    under_odds=1.90,
    home_odds=2.10,
    draw_odds=3.30,
    away_odds=3.60,
)
```

Replace every fixture ID, prediction ID, commit SHA, provider and URL with actual
verified values. The example odds are test values only, not current market data.

## What it does

1. Rejects missing IDs, missing source information, unverified odds, naive timestamps
   and prediction timestamps at/after kickoff.
2. Calls the V5.3 direct score-matrix candidate.
3. Derives 1X2, scoreline, draw, total-goal and net-margin outputs from one matrix.
4. Appends the prediction to the append-only `prediction_trace` table.
5. Appends the supplied market values and their source/timestamp metadata to
   `market_trace`. Provider event time is kept separate from actual retrieval time.
6. Returns the prediction plus archive identifiers and configuration SHA-256.

## Important limits

- This is a Python callable interface, not an HTTP server.
- It is not yet called by a daily scheduler or an existing live production runner:
  the repository audit did not identify a verified daily prediction entrypoint.
- It does not fetch fixtures or odds. The caller must supply verified pre-kickoff
  odds and the repository's verified internal `match_id`.
- It does not write or modify V5.1 predictions. The database writer rejects
  UPDATE/DELETE on trace rows.
- V5.3 remains an experimental direct-rule candidate. The integration tests must
  pass, and a real fixture must complete the prediction -> market snapshot -> result
  ID join before this can be described as an operational production interface.
