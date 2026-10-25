# Weekly football model backtest & xG integrity audit — 2026-W43

**Status: PARTIAL — formal V5.1 backtest blocked; ISO week still open at cutoff.**

- Audit cutoff: 2026-10-25T12:14:57+08:00. Week: 2026-10-19 through 2026-10-25 (ISO W43), using Beijing date to stay consistent with the existing archive.
- Completed results verified in this run: **50 league matches** across the seven requested leagues, plus **36 archived UEFA Europa/Conference League results** kept in a separate continental-club stratum. The Toulouse–Troyes fixture was postponed to later on 25 October local time and was not counted as completed or backtested.
- Shadow forecast sample: **21** completed matches with archived forecast values. 16 completed forecast rows from the Git-archived 24 October shadow JSON were eligible; its Toulouse–Troyes forecast was rescheduled to 25 October local time (26 October Beijing date) and excluded. Five earlier rows exist only inside an archived review JSON (the original individual snapshot files were not found). **None is a formal V5.1 prediction snapshot.**
- Formal V5.1 eligible sample: **0**. The archived prediction table does not provide a complete append-only trace with verified provider match ID, calibrated probability triplet, lambdas, model commit/config and official pre-match Sporttery handicap for these matches. Do not present the shadow metrics as V5.1 accuracy.
- No model, V5.1 parameters, historical prediction, or frozen validation sample was changed.

## Shadow metrics (not V5.1)

| Metric | Hits | Sample | Rate |
|---|---:|---:|---:|
| 90-minute H/D/A | 12 | 21 | 57.14% |
| First score exact | 4 | 21 | 19.05% |
| Second score exact | 4 | 21 | 19.05% |
| Half/full-time | 7 | 21 | 33.33% |
| Verified official Sporttery handicap | 0 | 21 | N/A — no official lines |
| Brier Score / Log Loss | — | 0 complete probability triplets | N/A |

By shadow version:
- `manual-shadow-v0.1`: H/D/A 1/5 (20.0%); score1 1/5; score2 1/5; half/full 1/5.
- `manual-shadow-v0.2`: H/D/A 11/16 (68.8%); score1 3/16; score2 3/16; half/full 6/16.

## Error diagnosis

- Actual absolute goal-difference bins: 0 = 5 (23.8%); 1 = 11 (52.4%); 2 = 4 (19.0%); 3+ = 1 (4.8%).
- Draw recognition: actual draws 5; predicted draws 6; correct draw calls 3; precision 50.0%; recall 60.0%; F1 54.5%.
- Result errors: 9 misses in total; 4 direct home↔away direction flips; 5 errors around the draw boundary (predicted draw but home win, or predicted non-draw but actual draw).
- Subjective confidence ≥60: 6 matches, 5 hits, 1 error (83.3% hit rate). The one high-confidence miss was Stuttgart–M'gladbach: predicted home win at 64, actual 2–3 away win. Confidence is not a calibrated probability.
- Handicap mapping errors cannot be counted: official Sporttery handicap is missing for all 21 shadow rows. Do not infer a line from Asian odds or a public odds page.

## Forced weekly xG audit

- Seven-league roster audited: **128 team-week rows**. Teams with at least one confirmed completed league match observed by cutoff: **83**.
- Completed league matches: **50**; home/away team-match sides: **100**. Only **1** match has an explicitly attributed provider xG pair, covering **2/100** team-match sides (2.00%). Team-week rows with complete provider xG for all observed completed matches: **2/128** (1.56%); among teams with a completed match, 2/83 (2.41%).
- The only provider-attributed pair found was Ipswich 0.46 xG / Nottingham Forest 2.29 xG, attributed to Opta via Fantasy Premier League by MyFootballFacts. Supplier version is not disclosed; retrieval timestamp is recorded, not mislabelled as provider event time. The Guardian independently reports Forest xG 2.31, a 0.02 difference; retained as a source-consistency warning.
- Six additional matches have public xG numbers without supplier/version attribution. Together with the Guardian cross-check and multiple conflicting Atlético reports, nine unverified secondary report rows are stored separately in `secondary-xg-reports.csv` and excluded from verified supplier weekly totals. Atlético–Deportivo reports are internally conflicting (1.44–2.02 versus 1.41–1.45). Do not average or silently select a value.
- For all other team-match rows, xG/xGA/xGD remain blank with a missing reason; final score is never used as an xG substitute. Weekly totals are left blank when the per-match supplier detail is incomplete. Only Ipswich and Nottingham Forest can be recalculated from the currently captured provider-attributed match detail.
- UEFA Europa League / Conference League: 36 confirmed results, 72 team-week rows, **0 provider xG pairs** and **0 immutable pre-match prediction snapshots**. These rows are kept separate from the seven-league totals. Other UCL/AFC result coverage was not complete in the branch and is marked unassessed, not zero.
- The ISO week was still open at cutoff; team match counts are completed matches observed as of the cutoff, not final week totals. Matches later on Sunday and the postponed Toulouse–Troyes fixture need a later refresh.

## Snapshot and leakage controls

- Database access limitation: the repository SQLite file is visible, but a live SQLite connection/query was not performed in this run; no DB-derived counts or joins are claimed. This audit uses Git-archived JSON/CSV and verified public result sources. The checked-in prediction-snapshot schema does not contain the complete append-only fields required for a formal V5.1 traceability record.

- The 16 forecasts from the 24 October JSON are Git-archived shadow rows, not V5.1; the 5 earlier forecasts are present only in a review JSON, not their original individual files.
- No calibrated probability triplet or lambdas were present, so Brier/Log Loss are not calculated. No official pre-match Sporttery line was verified, so handicap outcomes are N/A.
- All xG values captured here are post-match audit observations. They are **not** fed into the forecasts that were made before kickoff; no historical prediction was overwritten.

## Sources used

- EPL: https://www.skysports.com/football/ipswich-vs-n-forest/report/559520; https://www.theguardian.com/football/live/2026/oct/24/tottenham-arsenal-wsl-premier-league-efl-football-clockwatch-live; https://www.theguardian.com/football/live/2026/oct/24/chelsea-v-tottenham-premier-league-live
- La Liga: https://insights.betfred.com/football/competitions/spain/laliga/results/; https://www.oddalerts.com/livescores/deportivo-alaves-vs-malaga/4679-4677; https://results.fast/results/football-racing-santander-vs-espanyol-2026-10-24; https://elpais.com/deportes/futbol/2026/10/24/el-elche-aprovecha-el-lio-de-vallecas-y-golea-al-rayo.html; https://www.futbol24.com/match/2026/10/24/national/Spain/Primera-Division/2026-2027/Valencia/vs/Villarreal; https://www.laliga.com/en-GB/clubs/atletico-de-madrid/results
- Bundesliga: https://www.bundesliga.com/en/bundesliga/news/vfb-stuttgart-borussia-monchengladbach-match-report-highlights-matchday-7-39693; https://www.footballwebpages.co.uk/german-bundesliga/fixtures-results/october
- Serie A: https://www.legaseriea.it/serie-a/news/cagliari-bologna-2026-2027-0-2-cronaca-risultato-gol; https://sscnapoli.it/en/match-report-napoli-roma-3-3/; https://insights.betfred.com/football/competitions/italy/serie-a/results/
- Ligue 1: https://www.goal.com/en/competition/ligue-1/fixtures-results/dm5ka0os1e3dxcp3vh05kmp33; https://www.reuters.com/sports/soccer/hazard-winner-lifts-lens-2-1-win-over-paris-fc-2026-10-24/
- K League 1: https://www.flashscore.ca/soccer/south-korea/k-league-1/results/; https://www.yna.co.kr/amp/view/AKR20261024037100007
- J1 League: https://www.jleague.jp/en/j1/match/; https://sportscore.com/football/competition/japan/japanese-j1-league/z318q66hl1qo9jd/2026/
- UEFA Europa League / Conference League: https://fr.uefa.com/uefaeuropaleague/news/02a8-217720a96899-881b03afe0db-1000--ligue-europa-2026-27-calendrier-et-resultats/; https://www.uefa.com/uefaconferenceleague/news/02a8-2174cb200f25-b94693ddff4e-1000--202627-conference-league-all-the-league-phase-fixtures/
- xG provenance cross-check: https://www.myfootballfacts.com/match/english-premier-league-2026-2027-ipswich-town-vs-nottingham-forest-2026-10-24/; https://www.theguardian.com/football/2026/oct/23/ipswich-nottingham-forest-premier-league-match-report; https://totalodds.co.uk/matches/stuttgart-vs-borussia-monchengladbach-6553731; https://totalodds.co.uk/matches/torino-vs-monza-6553732; https://statsbet.org/football/matches/deportivo-alav-s-vs-m-laga-2026-10-23; https://statsnext.com/es/la-liga/matches/2026-10-24-racing-santander-vs-espanyol-98dec649; https://dailygoal.tv/m/3OM_LybFyat/napoli-vs-as-roma; https://www.golazonews.com/en/match/1570425/atletico-madrid-vs-deportivo-la-coruna; https://cornerflick.com/football/la-liga/matches/atletico-madrid-vs-deportivo-la-coruna-la-liga-2026-10-24
