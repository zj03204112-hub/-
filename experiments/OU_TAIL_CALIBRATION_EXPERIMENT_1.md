# V5.1 O/U Tail Calibration — Experiment 1

Status: **shadow experiment only; not promoted to production**.

## Scope
- Total-goal bins: 0–1, 2, 3–4, 5–6, 7+.
- Absolute goal-difference bins: 0, 1, 2, 3+.
- Calibrate by training-sample observed/predicted bin mass ratios with shrinkage.
- Preserve baseline predictions; apply calibrator only to a copied probability mass function.
- Asian total settlement supports half-goal and quarter-goal lines.

## Data gate
Do not fit or claim improved accuracy until the dataset has immutable, pre-kickoff O/U line + Over/Under prices, source/provider event ID, retrieval timestamp, match ID, and final score. Preferred market snapshot is T-6 to T-12 hours. Never backfill missing pre-match prices with closing or live prices. Mark missing rows as MISSING_OU_MARKET.

## Required validation
Use a chronological split; fit calibrators only on the training portion. Evaluate on untouched later matches. Report:
1. Goal-bin log loss and expected-total-goals MAE.
2. Exact-score probability log loss and Score 1 / Score 2 hit rates.
3. 90-minute H/D/A accuracy, Brier score and log loss.
4. Absolute/signed net-goal bins and official Sporttery handicap H/D/A.
5. O/U settlement accuracy and ROI only when line, both prices and settlement convention are verified.

Acceptance gate: total-goal distribution log loss must improve; H/D/A Brier, H/D/A log loss, and exact-score probability log loss must not worsen. If any gate fails, reject the calibration. No tuning on the final test split.

## Safety invariants
- No edits to official V5.1 production logic or historical snapshots.
- No post-match data as pre-match input.
- Handicap H/D/A is calculated from verified Sporttery handicap and Score 1 only.
- Score 2 is never used to settle the handicap.
- Do not claim a model run, database write, odds coverage, or accuracy gain unless logs and data prove it.

## Current blocker
The existing repository rule file says the pipeline is blocked until real provider-timestamped O/U line + Over/Under prices are ingested. This experiment provides calibration utilities only; it does not fabricate missing market history or claim a completed backtest.
