# 2026-11-12 10:00 batch — xG coverage correction (run11 r1)

Capture time: 2026-11-12T11:11:21+08:00

## Results review
- Previous Beijing day: 34 tracked finished matches.
- Immutable pre-match snapshots: 2; provisional previews: 19; no snapshot: 13.
- Immutable-snapshot 90-minute W/D/L: 1/2; first score 0/2; second score 0/2; HT/FT 0/2.
- Official Sporttery handicap line verified: 0/2; handicap result unverified; no Asian handicap substitution.
- Provisional-preview diagnostics (not immutable): W/D/L 9/19, first score 3/19, second score 1/19, HT/FT 5/19; excluded from V5.1 accuracy claims.

## ISO 2026-W46 xG
- Audit batch: 114 matches (34 tracked previous-day, 61 additional national/continental candidates, 2 date-reclassified Slovak Cup candidates, 17 early current-day completions).
- Unique matches with accepted provider xG: 19/114; still missing all accepted provider xG: 95/114.
- Provider series: 20; provider team rows with xG: 40.
- Per-90 known for 16 Xgoals series; other providers' period scope remains null.
- No measured xG and no own-model xG were added.
- Previous-day tracked slate: 16/34 have xG; 18 missing (4 EFL Trophy, 2 WCL, 1 WEC, 4 J-League Cup, 6 Copa del Rey, 1 Copa Colombia).
- Current-day early results: 3/17 have accepted xG; 14 missing. OH Leuven-HB Køge search snippet was not accepted because the source page returned 403.
- All provider series are kept separate; post-match xG is not used as same-match pre-match input.

## Schedule
- Public schedule candidates: 20 fixtures on 12 Nov Beijing and 45 on 13 Nov Beijing.
- No confirmed fixtures in the seven core domestic leagues in this two-day window.
- UEFA Nations League is separate: eight fixtures fall on 13 Nov Beijing (01:00 x2, 03:45 x6); CONCACAF Nations League is separate.
- Three unresolved schedule groups: Piratas-CA La Paz, Torreense-Sparta Praha, and six WSL Cup fixtures.

## Integrity
- SQLite not connected; team and competition IDs remain provisional aliases.
- No historical prediction or xG snapshot overwritten; V5.1 and frozen validation samples untouched.
- The 15:00 odds/lineup refresh is not part of this 11:11 capture; no early prediction generated.
