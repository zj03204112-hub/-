import json, os, sqlite3, sys
from datetime import datetime, timezone
sys.path.insert(0, "data_pipeline")
from collect_player_data import scheduled_events, event_date, is_target_event, team_key, find_match, get

DB = "football_model_database.sqlite"
N = int(os.environ.get("DRAW_ABSENCE_N", "128"))
OUT = os.environ.get("DRAW_ABSENCE_OUT", "data/draw_absence_retrospective_128.json")

def main():
    con = sqlite3.connect(DB)
    rows = con.execute("""
      SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.home_score,r.away_score
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.home_score=r.away_score
      ORDER BY m.kickoff DESC,m.match_id DESC LIMIT ?
    """, (N,)).fetchall()

    match_index = {}
    dates = set()
    for mid,ko,h,a,hs,as_ in rows:
        md = ko[:10]
        match_index[(team_key(h),team_key(a),md)] = mid
        dates.add(md)

    result = []
    seen = set()
    api_calls = 0
    for d in sorted(dates):
        payload = scheduled_events(d)
        for e in (payload or {}).get("events", []):
            if is_target_event(e) is None:
                continue
            ed = event_date(e)
            if not ed:
                continue
            home = (e.get("homeTeam") or {}).get("name","")
            away = (e.get("awayTeam") or {}).get("name","")
            mid = match_index.get((team_key(home),team_key(away),ed))
            if not mid or mid in seen:
                continue
            target = next((x for x in rows if x[0] == mid), None)
            if not target:
                continue
            seen.add(mid)
            event_id = str(e.get("id"))
            lineup = get(f"/event/{event_id}/lineups")
            api_calls += 1
            missing = {"home": [], "away": []}
            for side in ("home","away"):
                for mp in ((lineup or {}).get(side) or {}).get("missingPlayers") or []:
                    pl = mp.get("player") or {}
                    name = pl.get("name") or mp.get("name") or mp.get("playerName")
                    if name:
                        missing[side].append({
                            "player": name,
                            "player_id": str(pl.get("id") or mp.get("playerId") or ""),
                            "position": pl.get("position") or mp.get("position"),
                            "reason": mp.get("reason") or mp.get("type") or mp.get("status") or "missing"
                        })
            result.append({
                "match_id": mid, "kickoff": target[1],
                "home": target[2], "away": target[3],
                "actual_score": f"{target[4]}-{target[5]}",
                "provider_event_id": event_id,
                "missing_players": missing,
                "missing_count": len(missing["home"]) + len(missing["away"]),
                "evidence_type": "post_match_public_lineup",
                "pre_match_safe": False
            })

    for mid,ko,h,a,hs,as_ in rows:
        if mid not in seen:
            result.append({
                "match_id": mid, "kickoff": ko, "home": h, "away": a,
                "actual_score": f"{hs}-{as_}",
                "provider_event_id": None,
                "missing_players": {"home":[],"away":[]},
                "missing_count": 0,
                "evidence_type": "not_found",
                "pre_match_safe": False
            })

    result.sort(key=lambda x: x["kickoff"], reverse=True)
    summary = {
        "n_draws": len(rows),
        "matches_checked": len(result),
        "provider_lineups_found": sum(1 for x in result if x["provider_event_id"]),
        "draws_with_missing_players": sum(1 for x in result if x["missing_count"]>0),
        "total_missing_player_records": sum(x["missing_count"] for x in result),
        "pre_match_safe": False,
        "note": "Retrospective absence evidence only. This dataset must not enter T-12h prediction features or calibration."
    }
    out={"definition":"Retrospective check of public final-lineup missingPlayers for the 128 actual draws; no prediction inputs are changed.", "summary":summary, "matches":result}
    with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps(summary,ensure_ascii=False))
    con.close()

if __name__ == "__main__":
    main()
