import json, os, sqlite3, sys
from datetime import datetime, timedelta

sys.path.insert(0, "data_pipeline")
from compare_models import run_model, metrics_add, finish

DB = "football_model_database.sqlite"
OUT = os.environ.get("LEAGUE_HOLDOUT_OUT", "data/j1_k1_independent_holdout.json")
START = os.environ.get("LEAGUE_HOLDOUT_START", "2026-09-21")

# Explicit team aliases observed in the repository. This is deliberately
# separated from the 128-draw diagnostic and is never used for fitting.
J1 = {
    "長崎","Ｃ大阪","川崎Ｆ","鹿島","東京Ｖ","千葉","町田","横浜FM",
    "福岡","水戸","清水","FC東京","Ｇ大阪","京都","広島"
}
K1 = {
    "Gwangju FC","Jeonbuk Motors","Gangwon FC","FC Anyang",
    "Daejeon Citizen","Incheon United","Gimcheon Sangmu FC",
    "Ulsan Hyundai FC","FC Seoul","Pohang Steelers","Jeju United FC"
}

def bucket(team_h, team_a):
    if team_h in J1 or team_a in J1:
        return "J1"
    if team_h in K1 or team_a in K1:
        return "K1"
    return None

def main():
    con = sqlite3.connect(DB)
    rows = con.execute("""
      SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished'
        AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
        AND m.kickoff >= ?
      ORDER BY m.kickoff,m.match_id
    """,(START,)).fetchall()

    groups={"J1":[],"K1":[]}
    for row in rows:
        g=bucket(row[2],row[3])
        if g: groups[g].append(row)

    result={"definition":"Independent chronological holdout: completed J1/K League 1 matches on/after LEAGUE_HOLDOUT_START. No fitting, parameter selection, or draw-cause labels from this sample are allowed.","start":START,"groups":{}}

    for g, matches in groups.items():
        d={"n":0,"correct":0,"brier":0.0,"logloss":0.0}
        draws={"n":0,"predicted_draw":0,"draw_probability_sum":0.0}
        detail=[]
        for m in matches:
            p,a=run_model(con,"v4",m)
            metrics_add(d,p,a)
            if a=="D":
                draws["n"]+=1
                draws["predicted_draw"]+=int(max(p,key=p.get)=="D")
                draws["draw_probability_sum"]+=p["D"]
            detail.append({"match_id":m[0],"kickoff":m[1],"home":m[2],"away":m[3],"actual":a,"p":p,"pick":max(p,key=p.get)})
        summary=finish(d)
        summary["actual_draws"]=draws["n"]
        summary["draw_pick_rate_on_actual_draws"]=round(draws["predicted_draw"]/draws["n"],4) if draws["n"] else None
        summary["mean_draw_probability_on_actual_draws"]=round(draws["draw_probability_sum"]/draws["n"],4) if draws["n"] else None
        result["groups"][g]={"summary":summary,"matches":detail}

    result["total_n"]=sum(v["summary"]["n"] for v in result["groups"].values())
    result["warning"]="This report is descriptive validation only. It does not alter V4, fit a draw bias, or use the sample for calibration."
    with open(OUT,"w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v["summary"] for k,v in result["groups"].items()},ensure_ascii=False))
    con.close()

if __name__=="__main__":
    main()
