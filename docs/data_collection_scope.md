# Football Match Data Collection Scope

Effective scope for routine match records. Keep collection deliberately limited to the fields below; do not expand the routine record with odds, referee, weather, news, motivation, injury narratives, or other auxiliary fields unless separately requested.

## Required fields per match

1. **Match identity**: stable internal match ID, competition, kickoff date/time, home team, away team.
2. **Full-time result**: home goals, away goals, and 90-minute outcome (home win / draw / away win). Use the competition's official definition; do not mix extra time or penalties into a 90-minute result.
3. **Final score**: exact full-time score, stored as home goals and away goals.
4. **Players who appeared**: starting XI and substitutes who actually entered the match, linked to their team. Distinguish starters from substitutes; do not treat a predicted lineup as an actual appearance.
5. **Match xG, both teams**: home attacking xG and home defensive xG conceded; away attacking xG and away defensive xG conceded. Prefer one consistent provider per competition where possible. Store provider/source and source match ID so values are traceable. Do not substitute goals for xG or invent missing xG values.

## Data quality rules

- Store missing fields as null / unavailable, never as zero unless zero is the reported value.
- Preserve source, retrieval timestamp, and provider event ID for verification.
- Deduplicate by stable match identity and team mapping.
- Only mark a match completed after a reliable source confirms the final result.
- Keep historical prediction snapshots and the V5.1 model unchanged; this collection-scope note does not authorize model changes or rewriting prior predictions.
- The routine match record is complete when result/score, actual appearances, and both teams' attacking/defensive xG have been captured or explicitly marked unavailable.
