# Goal-total uplift experiment (shadow only)

## Hypothesis parameters
- Base total-goals lambda uplift: **+8%**.
- If the **original, pre-adjustment** total lambda is **>= 2.8**, apply a further **+10% multiplicative** uplift.
- Thus the combined multiplier above the threshold is 1.08 × 1.10 = **1.188** (+18.8%), not +18%.
- Preserve the original home/away lambda ratio; do not arbitrarily assign all extra goals to the home side.

Examples:
- Original total lambda 2.50 → adjusted total 2.70.
- Original total lambda 2.79 → adjusted total 3.0132 (base uplift only).
- Original total lambda 2.80 → adjusted total 3.3264 (both multipliers).

## Safety and evaluation
This is a deliberately blunt hypothesis to test, not a claim that higher goal estimates improve accuracy. It must remain isolated from production V5.1 and must not overwrite historical predictions or frozen validation data. Save original and adjusted lambdas side by side.

Before any promotion, evaluate against a time-separated, frozen sample with actual pre-match inputs. Report:
- total-goal distribution log loss and Brier score;
- goal-count bins 0–1, 2, 3–4, 5–6, 7+;
- exact score-1 and score-2 hit rates;
- 90-minute H/D/A Brier and log loss;
- Asian totals settlement only where the authentic pre-kickoff line and both-side prices are timestamped and verified.

Reject the change if total-goal calibration fails to improve or H/D/A and score probability metrics materially worsen. This module does not ingest odds and is not connected to the production predictor.
