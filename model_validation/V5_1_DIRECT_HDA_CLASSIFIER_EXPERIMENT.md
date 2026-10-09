# V5.1 Direct H/D/A Classifier Experiment

Status: experiment-only; not production.

## Purpose
Test whether a direct three-class H/D/A classifier adds value over the existing frozen V4 probability output. The classifier is deliberately separated from the production V5.1 path.

## Protocol
- Input: `model_validation/500_match_V4_frozen_predictions.csv`.
- Exactly 500 unique `match_id` rows are required.
- Sort by kickoff and use the first 300 for training, next 100 for model selection, and final 100 as untouched holdout.
- No shuffling.
- Features are restricted to pre-match fields: H/D/A probabilities, strength values/gaps, predicted goal difference, and top-score probabilities.
- Post-match scores, actual goal difference, and labels are never features.
- Compare accuracy, draw recall, multiclass Brier score, log loss, and confusion matrix.
- Choose regularization only on the calibration split; report final holdout once.
- No production files or live prediction behavior are changed.

## Acceptance
Promotion is prohibited unless final holdout accuracy improves and Brier, log loss, and draw recall do not worsen. Even if it passes this small holdout, require a further independent walk-forward validation before production promotion.

## Caveats
This is a compact classifier on an existing frozen prediction table, not a complete model rebuild from raw match history. It can test incremental value from probability/strength features, but cannot establish value from absent lineup, injury, tactical, or market-flow data.
