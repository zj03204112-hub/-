# Football model database audit

- Generated UTC: 2026-10-09T18:20:54+00:00
- Database file exists: True
- File size bytes: 63332352
- Audit status: COMPLETED

This report counts only the SQLite file present in this GitHub Actions checkout. Schema definitions are not treated as evidence that data exists.

## Match coverage

| Metric | Actual value |
|---|---:|
| total_matches | 8740 |
| status_counts | {'finished': 8740} |
| earliest_kickoff | 2022-01-01T12:30 |
| latest_kickoff | 2026-09-26 |
| distinct_match_ids | 8740 |
| duplicate_match_id_rows | 0 |
| null_match_id_rows | 0 |
| null_home_team_rows | 0 |
| null_away_team_rows | 0 |
| result_rows | 8740 |
| matches_with_result | 8740 |
| rows_with_final_score | 8740 |

## League/competition coverage

| League | Matches | Earliest | Latest |
|---|---:|---|---|
| La Liga | 1847 | 2022-01-02T13:00 | 2026-09-20T20:00 |
| English Premier League | 1780 | 2022-01-01T12:30 | 2026-09-20T16:30 |
| Serie A | 1768 | 2022-01-06T11:30 | 2026-09-20T19:45 |
| Ligue 1 | 1544 | 2022-01-07T20:00 | 2026-09-20T19:45 |
| Bundesliga | 1468 | 2022-01-07T19:30 | 2026-09-20T18:30 |
| K League 1 | 223 | 2026-04-25 | 2026-09-26 |
| J1 League | 110 | 2026-02-06 | 2026-09-20T17:03 |

## Table inventory

| Table | Rows | Columns |
|---|---:|---:|
| advanced_team_features | 17096 | 10 |
| collection_scope | 1 | 4 |
| competitions | 7 | 4 |
| injuries | 0 | 16 |
| lineup_projection | 17096 | 9 |
| matches | 8740 | 9 |
| player_features | 0 | 13 |
| player_match_stats | 29754 | 19 |
| prediction_snapshot | 9989 | 11 |
| provider_event_map | 705 | 4 |
| results | 8740 | 8 |
| review | 6 | 10 |
| schedule_intent | 16780 | 8 |
| schema_migrations | 1 | 2 |
| seasons | 34 | 5 |
| source_registry | 5 | 6 |
| sporttery_market | 81928 | 9 |
| team_features | 17096 | 10 |

## Module coverage

| Table | Exists | Rows | Distinct matches | Coverage |
|---|---|---:|---:|---:|
| results | True | 8740 | 8740 | 100.0% |
| sporttery_market | True | 81928 | 8263 | 94.54% |
| team_features | True | 17096 | 8548 | 97.8% |
| schedule_intent | True | 16780 | 8406 | 96.18% |
| injuries | True | 0 | 0 | 0.0% |
| prediction_snapshot | True | 9989 | 8548 | 97.8% |
| lineup_projection | True | 17096 | 8548 | 97.8% |
| provider_event_map | True | 705 | 705 | 8.07% |
| player_match_stats | True | 29754 | 700 | 8.01% |
| player_features | True | 0 | 0 | 0.0% |
| review | True | 6 | 6 | 0.07% |
| advanced_team_features | True | 17096 | 8548 | 97.8% |

## Player data coverage

{
  "rows": 29754,
  "distinct_matches": 700,
  "distinct_players": 4980,
  "xg_non_null_rows": 29754,
  "xg_positive_rows": 0,
  "xa_non_null_rows": 29754,
  "xa_positive_rows": 0,
  "rating_non_null_rows": 20077,
  "rating_positive_rows": 20077,
  "minutes_played_non_null_rows": 29754,
  "starter_non_null_rows": 29754
}

## Player data sources

| Source | Rows | Distinct matches |
|---|---:|---:|
| FotMob public matchDetails | 29754 | 700 |

## Missing expected tables

teams
