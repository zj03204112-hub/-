# Free J1 xG source cross-check — 2026-11-02

Branch: audit/stable-match-id-mapping-20261101

## Source found
Tifolab provides public J1 match pages with match-level xG, shots, shots on target, big chances and event timelines. Its public pages state that the displayed figures are match measurements; the site's coverage pages describe free web content and daily updates. It is an independent third-party source, not official J.League xG. Source data attribution on the site includes SportMonks. Record source/model provenance separately from official match-result provenance.

## Three match-level checks
- 2026-10-25 Kawasaki Frontale 2-1 Fagiano Okayama: Tifolab xG 0.82-1.04; existing legacy row 0.81-1.86.
- 2026-10-25 FC Tokyo 6-0 Mito Hollyhock: Tifolab xG 2.51-0.74; existing legacy row 2.60-1.04.
- 2026-10-31 Kashiwa Reysol 4-0 Urawa: Tifolab xG 1.25-1.45; existing legacy row 2.38-1.84.

Match-level source URLs are recorded in j1_2026_xg_tifolab_crosscheck_candidates.csv. The source page and fixture/date/score were matched for these three cases. The xG differences are material, so the old FootyStats-labelled values must remain marked unverified and must not be silently overwritten or combined with Tifolab values.

## xGA rule
The candidate file records xGA as the opponent's same-match Tifolab xG, explicitly labelled derived. This is not an independently published xGA field. Do not describe derived xGA as a separate provider measurement.

## Next verification steps
1. Expand Tifolab match-level checks to all 130 mapped J1 fixtures, matching exact date, home/away teams and final score.
2. Record source coverage gaps separately; never use team averages to fill missing fixture-level values.
3. Keep provider-specific xG in separate columns/source tables. Do not mix FootyStats and Tifolab xG as if they shared one model.
4. Only mark a row verified after source URL, fixture identity and both values have been reviewed. Preserve old values and provenance in the audit trail.
5. No V5.1 or historical prediction snapshot was changed.
