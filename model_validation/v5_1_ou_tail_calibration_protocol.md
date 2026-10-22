# V5.1 Shadow Experiment: O/U and Goal-Difference Tail Calibration

## Scope and safety

- This is an isolated experiment branch. It does not edit V5.1 production code, historical predictions, or the frozen 500-match sample.
- The runnable evaluation uses the repository's current `compare_models.model_lambdas(model="v4")` score-lambda source as the baseline. Before making any claim about the deployed V5.1, verify that this is exactly the same lambda generator used by that deployed version.
- No full-sample result is valid unless a unique frozen-500 mapping and at least 400/500 timestamped, complete O/U market snapshots captured at or before T-12h are available.
- Missing market data remains missing. No closing line, post-kickoff price, or other provider's fixture is substituted.

## Four layers

1. **Total-goal market calibration.** De-vig the Over/Under prices and find the common lambda scale that best matches the observed Asian-total line, including quarter-line half wins/losses. Apply a conservative fixed 25% of the scale change.
2. **Home/away allocation.** Scale both lambdas by the same factor. This uses the baseline attack/defence allocation and does not automatically raise home-team goals.
3. **Goal-difference tail.** On the chronological training slice only, estimate shrunken multiplicative factors for absolute goal-difference bins 0, 1, 2, and 3+. Apply factors to the same score matrix and renormalize.
4. **Out-of-sample gate.** Use the earliest 60% of complete market matches for fitting and the latest 40% for evaluation. Report H/D/A accuracy, Brier score and log loss, exact-score probability log loss, total-goal-distribution log loss, expected-total MAE, top-two exact-score hit rates, and modal-total accuracy.

## Acceptance rule

The shadow calibrator is accepted only if all conditions hold on the untouched chronological test slice:

- H/D/A Brier score does not worsen versus baseline.
- H/D/A log loss does not worsen versus baseline.
- Exact-score probability log loss does not worsen versus baseline.
- Total-goal-distribution log loss improves versus baseline.

Otherwise the result is `REJECT_OR_REVISE`. Do not tune the constants on the test slice.

## Run

From the repository root:

```bash
python -m unittest data_pipeline/test_v5_1_ou_tail_calibration_experiment.py
python data_pipeline/v5_1_ou_tail_calibration_experiment.py
```

The experiment writes `model_validation/v5_1_ou_tail_calibration_result.json`. If the database, mapping, market schema, or coverage is missing, it emits a blocked status rather than a fabricated result.
