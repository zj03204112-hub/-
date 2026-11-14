# V5.2 Shadow Experiment: Draw Calibration, Goal Tail, and Score Consistency

Date: 2026-11-14  
Branch: `experiment/draw-tail-score-consistency-2026-11-14`  
Status: `DESIGN_FROZEN_DATA_AUDIT_REQUIRED`  
Production V5.1: unchanged  
Historical prediction snapshots: read-only

## Objective

Run three isolated experiments and one combined shadow candidate. No candidate may replace V5.1 unless every pre-registered holdout gate passes and promotion is explicitly approved.

## Existing evidence and blockers

The 2026-11-09 first-round experiment used 5,505 finished legacy matches, with 5,009 eligible T-12 predictions and a chronological 60/20/20 split (3,005 development / 1,002 calibration / 1,002 untouched test). On the untouched test:
- Baseline H/D/A Brier: 0.606490; log loss: 1.013060.
- Baseline draw recall: 1.91%, despite mean predicted draw probability 25.634%.
- Baseline total-goal bucket log loss: 1.413507.
- The draw-only calibration selected zero bias and did not improve draw recall.
- The first tail multipliers worsened total-goal bucket log loss to 1.420996.
- Neither adjustment was accepted.

The prior audit also found that `data/market_lambda_backtest.csv` was absent; the 14-row displayed-prediction archive lacked stable match IDs, original probabilities/lambdas, and complete timestamped O/U snapshots. Therefore no historical O/U or draw-water movement result may be claimed until the raw source data is recovered and uniquely joined.

## Experiment A — independent draw identification and calibration

### Inputs
- No-vig pre-match 1X2 probabilities from a single identified bookmaker/source and market definition.
- At least two provider-timestamped, pre-kickoff observations for draw-odds movement; retain opening/current values and exact timestamps. Cross-provider rows or different market definitions are not a time series.
- No-vig O/U price and line, with exact settlement interpretation.
- Pre-match team/league draw-rate features calculated strictly before the prediction cutoff.
- Score-grid mass for 0-0, 1-1, 2-2 and all other diagonal scorelines.

### Method
1. Audit draw-price movement in decimal-odds points and implied-probability points separately. A 0.50 decimal-odds move must not be confused with a 0.50 probability move.
2. Measure whether movement adds out-of-sample information conditional on the market's current no-vig draw probability.
3. Fit a regularized three-class calibration model on the calibration slice. It must estimate H, D and A logits jointly from the full pre-match feature set; it must not apply a draw-only multiplier and dump residual probability into H/A, or transfer H/A probability into D by a fixed rule.
4. Report draw probability reliability by bins, draw precision/recall, PR-AUC, Brier, log loss, and the actual/predicted rates of 0-0, 1-1 and 2-2.
5. Preserve the calibrated probabilities as a complete triplet summing to one.

### A acceptance gate
- Draw recall must improve over the 1.91% first-round baseline without an unacceptable precision collapse.
- H/D/A Brier and log loss must not worsen on the untouched chronological holdout.
- Draw reliability and 0-0/1-1/2-2 probability calibration must be reported separately.
- The odds-movement feature is disabled for any fixture lacking comparable, timestamped pre-kickoff observations.

## Experiment B — total-goal distribution and high-goal tail

### Required bins
Use the exact six bins: `0`, `1`, `2`, `3`, `4`, `5+`. Keep the previous coarser bins only as a compatibility report, not as the fitting target.

### Inputs
- Verified pre-match O/U line, Over price, Under price, provider/bookmaker, event ID, market definition, provider event timestamp (if available), retrieval timestamp, and raw payload/hash.
- Opening and later pre-kickoff snapshots from the same provider and market definition when available.
- Team-specific pre-match attack/defence inputs and the original baseline lambdas.
- Quarter-line settlement split exactly into half-stake components; do not approximate quarter lines as half lines.

### Method
1. Build the baseline six-bin distribution from the score grid.
2. De-vig O/U prices and calculate expected settlement for the actual Asian total line, including quarter lines.
3. Derive line/price-consistent total-goal information and compare opening-to-current line and water changes.
4. Fit a regularized six-class calibration on the calibration slice. Use shrinkage and per-bin reliability diagnostics; do not hand-set universal multipliers or raise every favourite's scoring rate.
5. Preserve the original and calibrated six-bin probabilities, plus 4-goal and 5+-goal tail mass.
6. Missing, stale, post-kickoff, untimestamped, or ambiguously joined market records are excluded and counted by reason; never impute them from closing odds or final scores.

### B acceptance gate
- Six-bin log loss and multiclass Brier must improve on the untouched holdout.
- 4-goal and 5+-goal reliability must improve without materially damaging bins 0–3.
- Expected-total-goals MAE must not worsen.
- H/D/A Brier and log loss must not worsen after score-grid reconciliation.
- Results must replicate on a second untouched chronological window before acceptance.

## Experiment C — scoreline / goal-distribution / H-D-A coherence

### Required calculation order
1. Estimate `lambda_home` and `lambda_away` from pre-match team attack/defence and venue inputs; save each adjustment and its source.
2. Derive `lambda_total = lambda_home + lambda_away` and the baseline total-goal distribution.
3. Apply only accepted/validated market-conditioned total-goal calibration, retaining the original distribution.
4. Construct the complete joint score matrix, including the documented low-score correction (Dixon-Coles where validated).
5. Reconcile the matrix to the calibrated six total-goal bins without changing the total probability mass.
6. Derive H/D/A by summing the final score matrix: home = sum(h>a), draw = sum(h=a), away = sum(h<a).
7. Rank Score 1 and Score 2 from that same final matrix. No independent score or H/D/A override is allowed.

### C acceptance gate
- Probability mass sums to 1 within numerical tolerance.
- Six total-goal bins sum to 1 and match the matrix marginals.
- H/D/A probabilities sum to 1 and exactly equal the matrix's win/draw/loss sums.
- Score 1 and Score 2 are ranked from the same final distribution.
- H/D/A Brier/log loss and exact-score probability log loss do not worsen on the untouched holdout.
- Handicap outcomes, if reported, are derived mechanically from verified official Sporttery handicap and Score 1 only; Score 2 is never used.

## Combined shadow comparison

Run four named candidates on the same immutable match IDs:
1. Baseline (unchanged reference).
2. A only.
3. B only.
4. C coherence using only accepted A/B outputs, plus combined A+B+C.

Report:
- sample counts, unique match IDs, unmatched IDs, duplicate IDs, cutoff eligibility and source coverage;
- H/D/A accuracy, Brier, log loss, draw precision/recall and reliability;
- exact score probability log loss, Score 1/Score 2 hit rates;
- six-bin total-goal Brier/log loss, expected-total MAE, 4-goal and 5+-goal calibration;
- 0-0, 1-1, 2-2 calibration and absolute/net goal-difference bins 0/1/2/3+;
- provider, odds timestamp, data cutoff, model version, Git SHA, feature and parameter hashes.

## Data and validation protocol

- Use chronological 60% development / 20% calibration / 20% untouched test; do not tune on the test set.
- Add a second later untouched time window for replication.
- All feature and market snapshots must be strictly pre-kickoff and within the declared prediction window (target T-12h; separately label T-6h if used).
- Match joins require stable internal match_id plus verified provider event mapping. Never join on team names alone where ambiguous.
- Keep raw odds snapshots append-only; never replace a missing pre-match snapshot with closing/live/post-kickoff odds.
- Report coverage before fitting. If real historical market coverage is insufficient, mark the affected experiment `BLOCKED_MISSING_MARKET_DATA`; do not manufacture values.
- Keep V5.1 code, production entry point, historical predictions and original results unchanged.

## Immediate execution order

1. Re-run the read-only `sporttery_market` draw-odds movement audit and establish whether repeated rows are genuinely comparable, timestamped pre-match odds.
2. Audit O/U provider coverage, timestamps, quarter/half-line definitions, and stable match-ID joins. The prior `market_lambda_backtest.csv` absence is a hard blocker until a real source-backed replacement is built.
3. Build a single immutable joined sample with original prediction outputs, verified market snapshots and final results; publish eligible/unmatched/rejected counts before model fitting.
4. Implement and unit-test A, B and C independently on a new experiment branch.
5. Run chronological holdout and replication tests; retain all failed reports.
6. Do not merge or activate any candidate unless every relevant gate passes and the user explicitly approves promotion.
