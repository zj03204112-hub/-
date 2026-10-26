# Weekly team xG archive

## Data contract
- Key dimensions: team ID, competition ID, season ID, ISO week (Monday-Sunday), match ID, provider and provider version.
- Match rows retain xG, xGA, xGD, home/away, match date, provider event timestamp (when supplied), actual retrieval timestamp, raw payload reference/hash, measured-vs-model-estimated status, identity status and explicit missing reason.
- Provider-measured xG and in-house estimates must remain separate. Do not combine providers with different definitions into one series.
- A weekly summary is complete only after the full ISO-week fixture list and all source coverage statuses are reconciled. Never derive xG from final goals.
- Corrections are append-only. Do not overwrite historical match rows or weekly snapshots.
- For backtests, use only xG snapshots captured before the relevant kickoff.

## This run: 2026-W43
The initial audit file is deliberately partial: it records missing xG states for the 27 confirmed tracked-league matches dated 2026-10-25. It does not claim that these are all matches played during ISO week 43. Team IDs and internal match IDs are unresolved in this run; weekly totals remain null. The schema in `schema.sql` is proposed and has not been applied to the production SQLite DB because no live DB connection was available in this run.
