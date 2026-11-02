# J1 xG audit status — 2026-11-02

Branch: audit/stable-match-id-mapping-20261101

Re-read current repository files directly:
- xG table rows: 130.
- Rows with both home_xg and away_xg populated: 3.
- Rows marked missing_not_exposed_in_free_source: 127.
- The 3 populated rows are marked unverified_legacy_value; exclude them from model calibration until the individual source row is verified.
- The current audit JSON reports FootyStats access blocked with HTTP 403 and source_rows_matched=0.
- No scoreline-based imputation or team-average imputation is allowed.
- Existing xGA fields on the 3 legacy rows repeat home_xg for home_xga and away_xg for away_xga, rather than the opponent's xG. Treat these xGA values as invalid pending correction from a verified source.
- This audit does not modify V5.1 or historical prediction snapshots.
