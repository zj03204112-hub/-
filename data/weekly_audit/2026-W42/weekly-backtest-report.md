# Weekly football backtest and xG audit — 2026-W42

Status: PARTIAL / FORMAL_V5.1_BACKTEST_BLOCKED.

Audit window: 2026-10-12 through 2026-10-18 (ISO week 42). This report does not change V5.1, historical predictions, or frozen validation samples.

The repository contains no auditable immutable V5.1 prediction snapshots for this week. The available 2026-10-16 review CSV contains temporary chat predictions without stable match IDs, independent 90-minute outcome fields, full probability triplets, lambdas, or complete official pre-match handicap snapshots. Therefore formal V5.1 hit rates, Brier Score, Log Loss, handicap results, and half/full-time accuracy are N/A.

The weekly xG archive is incomplete. The repository schema has team_features.xg_for/xg_against, but data_pipeline/build_features.py writes None to both fields. No weekly xG snapshot archive with supplier/version/capture time/method/missing reason was found. The SQLite file is present in the repository, but this run did not establish a live SQLite connection to count its rows.

K League 1 source CSV has 80 rows whose Date column contains times such as 05:00/07:30, so they cannot be assigned to an ISO week. No model or parameter changes were made.
