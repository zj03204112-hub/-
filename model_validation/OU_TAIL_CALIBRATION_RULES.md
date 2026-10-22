# Persistent Rule: Future Football Predictions Use O/U-Calibrated Goal Distribution

**Effective for future prediction runs after the required market data is available.** This file records the requested calculation rule; it does not itself prove that market data has been collected, and it does not modify V5.1 production code.

## Required input snapshots

For every fixture, preserve the bookmaker/provider and event ID, match ID, competition, home/away teams, kickoff time and timezone, provider-supplied event timestamp, retrieval timestamp, market line, Over odds, Under odds, odds format, and raw source/payload. Keep each provider and each timestamp as a separate immutable snapshot. For the prediction workflow, prefer a valid snapshot captured 6–12 hours before kickoff. Never replace a missing pre-match snapshot with closing, live, or post-kickoff prices. If unavailable, record `MISSING_OU_MARKET` and do not claim O/U calibration was applied.

## Mandatory four-layer calculation

1. **Total goals:** de-vig Over/Under prices; interpret Asian totals correctly, including quarter lines and half-win/half-loss settlement; calibrate the total-goal probability distribution against the market line and both prices. Preserve the original and calibrated distributions.
2. **Home/away allocation:** allocate calibrated total-goal expectation using each side's attack and opponent-defence estimates, lineup/absence adjustments, venue, form and other pre-match inputs. Do not direct all extra goal expectation to the home side. Save both lambdas and the source of every adjustment.
3. **Goal difference:** independently calibrate absolute/net goal-difference bins 0, 1, 2, and 3+ (and retain the signed home/away outcome mapping). Keep outright win probability separate from handicap-cover probability. Handicap win/draw/loss must be mechanically derived from the official verified Sporttery handicap and **Score 1 only**; Score 2 must not determine the handicap result.
4. **Validation gate:** compare the unchanged baseline with the shadow-calibrated version on an untouched chronological test set. Report total-goal distribution log loss and expected-total MAE; exact-score probability log loss and Score 1/Score 2 hit rates; 90-minute H/D/A accuracy, Brier score and log loss; and net-goal bins/handicap outcomes. Accept the revised version only if total-goal distribution log loss improves and H/D/A Brier, H/D/A log loss, and exact-score probability log loss do not worsen. Otherwise reject/revise; do not tune on the test slice.

## Data integrity and model safety

- Keep V5.1 production code and existing historical prediction snapshots unchanged until the experiment passes the gate and is explicitly approved.
- Do not use post-match xG, final odds, final scores, or post-kickoff information as pre-match inputs.
- Do not fabricate or impute missing O/U lines, odds, provider timestamps, team IDs, or outcomes. Report coverage and missing reasons.
- Preserve a reproducible run record: code commit, model version, data cutoff, market snapshot IDs, training/test match IDs, parameters, metrics, and acceptance status.
- The current experiment is blocked until real provider-timestamped O/U line + Over/Under prices are ingested. A handicap-only Sporttery table is not a substitute.

## Next run checklist

1. Ingest and audit real O/U snapshots and event timestamps.
2. Join snapshots to the frozen sample by unique match ID; report matched/unmatched counts.
3. Check T-12h/T-6h cutoff eligibility and provider coverage before fitting.
4. Run unit tests, then baseline-vs-shadow evaluation.
5. Save the full JSON report and source/coverage audit; only promote after the acceptance gate passes.
