# Market lambda backtest input contract

The experiment consumes data/market_lambda_backtest.csv. One row must represent one unique, completed fixture and one immutable, genuine pre-kickoff snapshot.

Required columns:

- match_id: stable internal fixture ID; unique per row.
- kickoff_utc: ISO-8601 kickoff timestamp with timezone.
- snapshot_utc: ISO-8601 snapshot timestamp with timezone, strictly earlier than kickoff.
- snapshot_type: one of prematch, pre_match, or 赛前快照.
- snapshot_source: named bookmaker/provider/source.
- provider_event_id: source fixture/event identifier.
- payload_sha256: SHA-256 of retained raw provider response.
- home_goals, away_goals: verified 90-minute score.
- home_odds, draw_odds, away_odds: decimal 1X2 odds from the same snapshot.
- ou_line, over_odds, under_odds: total-goals line and both prices from the same source and snapshot.
- baseline_lambda_home, baseline_lambda_away: archived V5.1 baseline lambdas captured before kickoff, not reconstructed from final scores.

## Data integrity requirements

- Preserve the raw payload and provider event time in the source archive; snapshot_utc must not be invented from kickoff or result time.
- No missing O/U prices, no mixing different books/timestamps, and no closing/live/post-kickoff replacement.
- Do not reconstruct baseline lambdas from final scores.
- The backtest sorts by kickoff and reserves the latest 30% as untouched chronological holdout. Candidate is compared with baseline lambdas on the same holdout.
- Quarter lines (2.25/2.75) are not supported by the current Poisson inversion. Use half-goal lines (2.5/3.5) until split-line inversion is implemented.
- If a row is incomplete or cannot be proven pre-kickoff, reject it and report the reason.

## Current source coverage

The checked-in reviews/2026-10-16-prematch-market-snapshots.csv contains 1X2/handicap snapshots for some fixtures, but the checked rows have empty ou_line, over_odds, and under_odds fields. Those rows are not eligible for this experiment. Keep them missing rather than filling from post-kickoff data.
