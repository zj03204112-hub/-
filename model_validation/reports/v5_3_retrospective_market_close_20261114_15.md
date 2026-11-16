# V5.3 retrospective market-close replay — 2026-11-14 to 2026-11-15

## Scope and status

- Competition: UEFA Nations League, 16 matches across 14 and 15 November 2026.
- Model: the repository's exact V5.2 market-lambda functions plus V5.3 direct matrix repair weights; no training and no parameter fitting.
- Odds: Bet365 archived closing 1X2 and O/U 2.5 prices.
- Scores: final scores cross-checked against published results listings.
- Important limitation: the archive describes these as closing prices but does not expose the exact provider event timestamp for each odds row. Therefore this is a **retrospective close-odds replay**, not a strict timestamped T-minus replay. No artificial prediction timestamp was created and nothing was written to the immutable production trace store.
- The previous provisional 16-game computation used odds that were not individually source-linked. This report supersedes it.

## Results

| Metric | V5.3 replay |
|---|---:|
| 90-minute direction | 11/16 (68.75%) |
| First score exact | 0/16 |
| Second score exact | 2/16 |
| Actual draws | 2 |
| V5.3 H/D/A argmax predicted draws | 0 |
| Draw recall | 0/2 |
| Actual total goals 5+ | 6/16 (37.50%) |
| Mean V5.3 predicted total-goals 5+ probability | 19.83% |
| V5.3 H/D/A Brier | 0.4810 |
| Market de-vig H/D/A Brier | 0.4786 |
| V5.3 H/D/A log loss | 0.8298 |
| Market de-vig H/D/A log loss | 0.8235 |
| Matrix probability-mass checks | all passed |

The direction argmax count equals the market baseline's 11/16 on this sample. However, V5.3's H/D/A probability scores are slightly worse than the de-vigged market baseline (higher Brier and log loss). This small sample is diagnostic only, not evidence of general performance.

## Match-level V5.3 output

Probabilities are percentages. `lambda` is home/away expected goals.

| Date | Match | Top score 1 | Top score 2 | V5.3 H/D/A | Actual | λ home/away | P(5+ goals) | Direction |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 11-14 | Kosovo–Israel | 1-1 | 1-0 | 51.0/25.9/23.1 | 0-0 D | 1.69/1.04 | 15.3% | miss |
| 11-14 | Norway–Wales | 2-0 | 3-0 | 74.8/15.6/9.6 | 3-1 H | 2.71/0.88 | 31.0% | hit |
| 11-14 | Georgia–Hungary | 1-1 | 0-1 | 26.6/29.7/43.7 | 0-2 A | 0.98/1.34 | 9.1% | hit |
| 11-14 | Portugal–Denmark | 1-1 | 2-1 | 54.4/23.2/22.4 | 1-4 A | 1.98/1.18 | 22.7% | miss |
| 11-14 | Northern Ireland–Ukraine | 1-1 | 1-0 | 37.4/32.0/30.7 | 1-0 H | 1.12/0.97 | 6.7% | hit |
| 11-14 | Austria–Rep. of Ireland | 1-1 | 1-0 | 54.5/25.7/19.9 | 1-0 H | 1.71/0.91 | 13.6% | hit |
| 11-14 | Romania–Poland | 1-1 | 0-1 | 17.6/23.3/59.1 | 2-1 H | 0.96/1.94 | 17.6% | miss |
| 11-14 | Sweden–Bosnia-Herzegovina | 2-0 | 1-1 | 63.6/21.0/15.4 | 3-2 H | 2.16/0.95 | 21.7% | hit |
| 11-15 | Cyprus–Montenegro | 1-1 | 0-1 | 34.0/27.2/38.8 | 1-4 A | 1.34/1.44 | 15.7% | hit |
| 11-15 | Latvia–Armenia | 1-1 | 0-1 | 30.6/30.5/39.0 | 0-0 D | 1.06/1.23 | 8.7% | miss |
| 11-15 | Belarus–Albania | 0-1 | 1-1 | 23.2/31.0/45.8 | 0-2 A | 0.83/1.28 | 6.7% | hit |
| 11-15 | Finland–San Marino | 3-0 | 4-0 | 93.3/5.1/1.6 | 7-2 H | 3.93/0.47 | 47.0% | hit |
| 11-15 | Belgium–Italy | 1-1 | 2-1 | 45.0/25.7/29.3 | 4-1 H | 1.67/1.27 | 18.9% | hit |
| 11-15 | France–Türkiye | 3-0 | 2-0 | 80.1/12.4/7.5 | 2-1 H | 3.17/0.92 | 41.1% | hit |
| 11-15 | Croatia–Czechia | 2-0 | 1-0 | 67.9/20.1/12.0 | 2-1 H | 2.17/0.77 | 18.7% | hit |
| 11-15 | Spain–England | 1-1 | 2-1 | 43.0/24.9/32.2 | 2-3 A | 1.72/1.43 | 22.8% | miss |

## Main findings and next fixes

1. **Draw identification remains unresolved.** The top scoreline is often 1-1, but the total draw probability never becomes the largest H/D/A class in this sample. The model therefore still predicts zero draws as its 1X2 pick and misses both actual 0-0 results. A scoreline multiplier alone does not guarantee draw-class recall.
2. **High-goal tail remains materially underweighted.** Actual 5+ frequency is 37.5%, while the mean predicted probability is 19.83%. Finland–San Marino and France–Türkiye are high-tail examples; the largest shortfall is not solved by the current fixed 5/6/7/8+ multipliers.
3. **Exact-score ranking is weak.** Top-1 hit rate is 0/16; top-2 is 2/16. This is expected to be a difficult metric, but the current matrix still concentrates too much mass on 1-1 in several fixtures.
4. **Do not increase all draw or high-goal weights blindly.** First isolate the contribution of each rule with one-factor-at-a-time shadow runs and compare Brier/log loss, draw recall, 5+ calibration, and score-rank distribution.
5. **Do not promote V5.3 to production from this sample.** V5.3's H/D/A Brier and log loss are slightly worse than the de-vigged market baseline.

## Reproduction

Fixture input: `model_validation/fixtures/v5_3_unl_20261114_15.csv`

Run:

```bash
python -m model_validation.backtest_v5_3_market_close
```

The script uses the repository's V5.2 market-lambda and V5.3 score-matrix functions directly. It does not fabricate an exact market timestamp or write retrospective records into the production trace store.
