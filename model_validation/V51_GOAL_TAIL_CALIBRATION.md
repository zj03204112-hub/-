# V5.1 Goal-Total Tail Calibration — Shadow Experiment

Status: specification only; no production parameters changed and no accuracy claim made.

## Symptom to test
Predicted total goals cluster excessively at 2, 3, and 4. Diagnose whether this comes from (a) lambda shrinkage/over-regularization, (b) a Poisson-only distribution with under-dispersed tails, (c) incorrect O/U market calibration, (d) home/away lambda allocation, or (e) score ranking/rounding that reports only central outcomes.

## Required baseline audit
On a chronological, untouched validation slice, record for every match:
- pre-match home/away lambdas and total lambda;
- full probabilities P(T=k), k=0..8, and P(T>=9);
- top-2 score probabilities before and after any calibration;
- observed total-goal frequency for bins 0, 1, 2, 3, 4, 5, 6+;
- predicted-vs-observed calibration, total-goal log loss, ranked probability score (or multiclass Brier), and expected-total MAE;
- breakdown by league, home/away strength gap, favourite status, and competition type.

Do not diagnose from displayed Score 1/Score 2 alone: inspect the entire probability distribution.

## Shadow candidate A — distributional dispersion
1. Preserve baseline lambdas and baseline Poisson probabilities.
2. Fit a low-dimensional dispersion parameter on training data only. Compare Poisson against a negative-binomial total-goal model or a calibrated total-goal distribution; do not automatically choose the heavier-tailed model.
3. Estimate probabilities for totals 0, 1, 2, 3, 4, 5, 6+ with smoothing and chronological shrinkage to league priors.
4. If O/U line + Over/Under prices exist, remove bookmaker margin and use them as an independent calibration signal. Never fabricate missing lines/prices or replace pre-match snapshots with live/closing prices.

## Shadow candidate B — allocate totals to teams
Condition on calibrated total T and allocate goals between home/away using the pre-match attack/defence strengths and the original lambda ratio. Ensure P(home goals, away goals) sums to 1. Do not push all extra tail probability toward the home team. Preserve low-score dependence correction (e.g. Dixon–Coles) only if it improves held-out calibration.

## Tail and score reporting rules
- Keep 0–1 total goals (low tail), 2–4 (middle), and 5+ (high tail) as separate audit bins; additionally report 6+ to expose blowout tails.
- Keep net goal difference bins 0, 1, 2, 3+ independently calibrated, with signed home/away outcomes retained.
- Rank Score 1 and Score 2 by the final joint score probabilities, not by manually forcing variety. Report their probabilities.
- Handicap W/D/L must be mechanically computed from verified official Sporttery handicap and Score 1 only; Score 2 must not affect handicap settlement.
- Missing or stale market inputs must be explicitly flagged.

## Acceptance gate
Compare baseline and shadow candidate on the same untouched chronological holdout:
- total-goal log loss and ranked probability score / multiclass Brier;
- expected-total MAE;
- score probability log loss and Score 1/Score 2 exact-hit rates;
- 90-minute H/D/A accuracy, Brier and log loss;
- net-goal bins and handicap W/D/L.
Accept only if total-goal distribution quality improves and H/D/A Brier, H/D/A log loss, and score-probability log loss do not worsen. Reject or revise if the apparent tail improvement is only redistribution without better calibration. Do not tune on the holdout.

## Safety
This is an isolated experiment. Do not change V5.1 production code, overwrite historical predictions, or claim the model is fixed until real data is audited, the experiment is run, and the acceptance gate passes. Log commit SHA, dataset IDs, data cutoff, market snapshot IDs, parameters, metrics, and missing-data counts.
