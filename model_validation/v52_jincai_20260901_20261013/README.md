# V5.2 Unified Jincai Model sample/backtest

Date range: 2026-09-01 to 2026-10-13.

This folder records the current V5.2 Jincai sample/backtest summaries generated locally on 2026-10-13 HKT. No V5.1 files were modified and this was uploaded only to `feature/v5.2-classification-data-audit`.

## Uploaded summaries

- `v52_unified_backtest_summary_20260901_20261013.json`: official Sporttery completed-result sample summary. The official endpoint returned 548 completed rows, 542 with actual score. These rows are not fully mapped to V5.2 features because Sporttery Chinese team IDs/names are not bridged to the local English/FotMob identity.
- `v52_db_native_backtest_summary_20260901_20261013.json`: DB-native V5.2 replay summary. This is the reliable current model-coverage view.

## Key result

DB-native rows with Sporttery market coverage:

- n: 153
- 90-minute 1X2 accuracy: 48.37%
- exact-score hit rate: 15.69%
- Brier: 0.6206
- LogLoss: 1.0375
- draw recall: 0.00%
- prediction counts: H 85 / D 0 / A 68

The main blocker is unchanged: V5.2 predicted zero draws on this replay slice, so draw recall is 0.00%. This must be fixed before production Jincai use.

## Local CSV artifacts

The full CSV files were generated locally under `outputs/v52_jincai_20260901_20261013/`:

- `jincai_official_completed_results_20260901_20261013.csv`
- `v52_unified_predictions_backtest_20260901_20261013.csv`
- `v52_db_native_predictions_backtest_20260901_20261013.csv`

They were not uploaded in this connector pass because the current GitHub connector does not provide a direct local-file upload path for larger CSV artifacts. Use a repaired `gh auth login -h github.com` or normal git push to add them exactly.
