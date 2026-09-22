import json, sqlite3, math
from pathlib import Path

DB = "football_model_database.sqlite"
OUT = "data/advanced_feature_audit.json"


def cols(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def safe_mean(xs):
    xs = [float(x) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def main():
    con = sqlite3.connect(DB)
    tc = cols(con, "team_features")
    sc = cols(con, "schedule_intent")
    pc = cols(con, "player_match_stats")
    if not {"match_id", "team_side", "goals_for", "goals_against", "opponent_strength"} <= tc:
        raise SystemExit("team_features schema incomplete")
    if not {"match_id", "team_side", "next_opponent", "rest_days", "rotation_risk"} <= sc:
        raise SystemExit("schedule_intent schema incomplete")
    required_player = {"match_id", "team_side", "player_id", "minutes_played", "rating", "xg", "xa", "starter"}
    player_ready = required_player <= pc

    con.execute("""CREATE TABLE IF NOT EXISTS advanced_team_features (
        match_id TEXT NOT NULL, team_side TEXT NOT NULL,
        player_impact REAL NOT NULL DEFAULT 0,
        squad_xg90 REAL NOT NULL DEFAULT 0,
        squad_xa90 REAL NOT NULL DEFAULT 0,
        player_stability REAL NOT NULL DEFAULT 0,
        fatigue_load REAL NOT NULL DEFAULT 0,
        next_opponent_strength REAL NOT NULL DEFAULT 0,
        rotation_risk REAL NOT NULL DEFAULT 0,
        true_strength_proxy REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(match_id, team_side)
    )""")
    con.execute("DELETE FROM advanced_team_features")

    matches = con.execute("""SELECT match_id,kickoff,home_team,away_team
        FROM matches WHERE status='finished' ORDER BY kickoff,match_id""").fetchall()
    rows = []
    coverage = {"matches": len(matches), "player_ready": player_ready, "rows": 0,
                "player_rows_used": 0, "no_future_player_data": True}

    for mid, kickoff, home, away in matches:
        for side, team in (("home", home), ("away", away)):
            tf = con.execute("""SELECT goals_for,goals_against,opponent_strength
                FROM team_features WHERE match_id=? AND team_side=?""", (mid, side)).fetchone()
            si = con.execute("""SELECT next_opponent,rest_days,rotation_risk
                FROM schedule_intent WHERE match_id=? AND team_side=?""", (mid, side)).fetchone()
            if not tf:
                continue
            gf, ga, opp_strength = tf
            next_opp, rest_days, rotation_risk = si if si else (None, 0, 0)
            next_strength = 0.0
            if next_opp:
                # Only information already available in team_features is used.
                r = con.execute("""SELECT AVG(opponent_strength) FROM team_features tf
                    JOIN matches m ON m.match_id=tf.match_id
                    WHERE tf.team_side IN ('home','away') AND
                          (m.home_team=? OR m.away_team=?) AND m.kickoff < ?""",
                    (next_opp, next_opp, kickoff)).fetchone()[0]
                next_strength = float(r or 0.0)

            player_impact = squad_xg90 = squad_xa90 = stability = fatigue = 0.0
            if player_ready:
                prs = con.execute("""SELECT p.rating,p.xg,p.xa,p.minutes_played,p.starter,m.kickoff
                    FROM player_match_stats p JOIN matches m ON m.match_id=p.match_id
                    WHERE p.team_side=? AND m.kickoff < ?
                    ORDER BY m.kickoff DESC LIMIT 80""", (side, kickoff)).fetchall()
                coverage["player_rows_used"] += len(prs)
                if prs:
                    ratings = [r[0] for r in prs if r[0] is not None]
                    player_impact = 0.60*safe_mean(ratings) + 0.25*safe_mean([r[1] for r in prs]) + 0.15*safe_mean([r[2] for r in prs])
                    mins = sum(float(r[3] or 0) for r in prs)
                    squad_xg90 = safe_mean([(r[1] or 0)*90/max(float(r[3] or 0),1) for r in prs if r[3]])
                    squad_xa90 = safe_mean([(r[2] or 0)*90/max(float(r[3] or 0),1) for r in prs if r[3]])
                    if len(ratings) >= 2:
                        mu = sum(ratings)/len(ratings)
                        stability = math.sqrt(sum((x-mu)**2 for x in ratings)/len(ratings))
                    recent = con.execute("""SELECT COALESCE(SUM(p.minutes_played),0) FROM player_match_stats p
                        JOIN matches m ON m.match_id=p.match_id
                        WHERE p.team_side=? AND m.kickoff < ? AND m.kickoff >= datetime(?, '-21 days')""",
                        (side, kickoff, kickoff)).fetchone()[0]
                    fatigue = min(1.0, float(recent or 0)/1890.0)

            true_strength = float(opp_strength or 0) + 0.20*float(gf or 0) - 0.20*float(ga or 0)
            rows.append((mid,side,player_impact,squad_xg90,squad_xa90,stability,fatigue,next_strength,float(rotation_risk or 0),true_strength))

    con.executemany("""INSERT INTO advanced_team_features
        (match_id,team_side,player_impact,squad_xg90,squad_xa90,player_stability,
         fatigue_load,next_opponent_strength,rotation_risk,true_strength_proxy)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    coverage["rows"] = len(rows)
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(coverage, ensure_ascii=False))
    con.close()


if __name__ == "__main__":
    main()
