# V5.3 high-goal-tail ablation diagnostics — 2026-11-16

## Scope and data quality

- Reuses the 16 UEFA Nations League matches dated 2026-11-14/15 in `v5_3_unl_20261114_15.csv`.
- Odds are archived Bet365 closing prices; exact provider event timestamps are unavailable.
- This is a retrospective close-odds replay, not a strict T-12 or timestamp-verified pre-kick replay.
- No training or parameter fitting. All multipliers are fixed sensitivity probes.
- The 16-match set is diagnostic only and must not be used to select production weights.

## Key finding: the tail is misallocated, not simply too small

Observed actual totals:

| Total goals | Actual count | Actual frequency |
|---|---:|---:|
| 0 | 2 | 12.5% |
| 1 | 2 | 12.5% |
| 2 | 2 | 12.5% |
| 3 | 3 | 18.75% |
| 4 | 1 | 6.25% |
| 5 | 5 | 31.25% |
| 6 | 0 | 0% |
| 7 | 0 | 0% |
| 8+ | 1 | 6.25% |

Baseline mean predicted distribution:

| Total goals | Mean predicted probability |
|---|---:|
| 0 | 6.5% |
| 1 | 15.5% |
| 2 | 22.5% |
| 3 | 20.2% |
| 4 | 15.4% |
| 5 | 10.0% |
| 6 | 5.5% |
| 7 | 2.6% |
| 8+ | 1.8% |

The key structural mismatch is exact-five probability (10.0% predicted vs 31.25% observed) versus 6+ probability (9.9% predicted vs 6.25% observed). A single multiplier applied to every 5+ bucket is too crude: it raises 6/7/8+ at the same time as exactly five.

## Fixed sensitivity checks (independent local reproduction)

| Scenario | Mean P(5+) | Mean P(exactly 5) | Mean P(6+) | H/D/A Brier | H/D/A LogLoss | Total-goals 9-class Brier | Total-goals 9-class LogLoss |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline V5.3 | 19.83% | 10.0% | 9.9% | 0.4810 | 0.8298 | 0.8439 | 1.9074 |
| All 5+ buckets ×3.0 | 38.31% | 19.9% | 18.4% | 0.4700 | 0.8063 | 0.8061 | 1.8257 |
| Exactly 5 only ×3.0 | 31.2% | 23.1% | 8.1% | 0.4710 | 0.8091 | 0.7980 | 1.7619 |
| Exactly 5 only ×4.5 | 37.8% | 30.7% | 7.1% | 0.4667 | 0.7991 | 0.7887 | 1.7439 |

* H/D/A values in this diagnostic use the unchanged joint matrix calculation; exact figures are to be reproduced by the committed ablation script in CI before treating the report as verified. The local reproduction at ×4.5 produced H/D/A Brier 0.4667 and LogLoss 0.7991; at ×3.0 it produced approximately 0.4700/0.8063. Values rounded.

The exactly-five-only adjustment produces a better distributional shape than a uniform 5+ uplift on this sample. However, the apparently strong ×4.5 result is a sensitivity result on only 16 retrospective matches and is highly vulnerable to sample-specific overfit. It is not a production recommendation.

## Next validation steps

1. Reproduce all scenarios with `python -m model_validation.ablation_v5_3_tail` in GitHub Actions.
2. Obtain a larger, independent, timestamp-verified pre-kick sample spanning several competitions and market lines.
3. Check exact-5, 6+, 8+, away-goal and scoreline calibration separately; compare 9-class total-goal Brier/LogLoss, binary 5+ scores, H/D/A scores and top-two score hits.
4. Only consider a bounded rule if it improves proper scoring on a later untouched time split and does not harm other bins.
5. Keep V5.1 frozen and V5.3 experimental until that validation passes.

## Reproduction command

```bash
python -m model_validation.ablation_v5_3_tail
```
