import json, sqlite3, re, unicodedata
from pathlib import Path
DB="football_model_database.sqlite"; SRC=Path("data/manual_handicap_reviews.json")
ALIASES = {
    "马竞":["Atletico Madrid","Ath Madrid","Atl Madrid"], "皇马":["Real Madrid"],
    "尼斯":["Nice"], "里尔":["Lille"], "富勒姆":["Fulham"],
    "曼联":["Man United","Manchester United"], "沙尔克04":["Schalke 04","Schalke"],
    "埃尔弗斯贝格":["Elversberg"], "尤文图斯":["Juventus"], "亚特兰大":["Atalanta"],
    "比利亚雷亚尔":["Villarreal"], "莱万特":["Levante"],
    "拉科鲁尼亚":["La Coruna","Deportivo La Coruna","Deportivo"],
    "皇家贝蒂斯":["Betis","Real Betis"], "帕德博恩":["Paderborn"],
    "霍芬海姆":["Hoffenheim"], "AC米兰":["AC Milan","Milan"], "莱切":["Lecce"],
    "马赛":["Marseille"], "巴黎圣日耳曼":["Paris SG","Paris Saint-Germain","PSG"],
    "巴伦西亚":["Valencia"], "皇家社会":["Sociedad","Real Sociedad"],
}
def norm(s):
    s=unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+","",s)

def find_match(con,r):
    homes=ALIASES.get(r["home"],[r["home"]]); aways=ALIASES.get(r["away"],[r["away"]])
    # Kickoff timestamps in DB may be UTC while manual reviews use local match dates.
    # Search a ±1-day window, then require both normalized team names to match an alias.
    rows=con.execute("""
      SELECT match_id,kickoff,home_team,away_team
      FROM matches
      WHERE date(kickoff) BETWEEN date(?,'-1 day') AND date(?,'+1 day')
      ORDER BY abs(julianday(date(kickoff))-julianday(date(?))), kickoff
    """,(r["date"],r["date"],r["date"])).fetchall()
    hn=[norm(x) for x in homes]; an=[norm(x) for x in aways]
    def ok(db, aliases):
        nd=norm(db)
        return any(a and (a in nd or nd in a) for a in aliases)
    matches=[row for row in rows if ok(row[2],hn) and ok(row[3],an)]
    return matches[0] if matches else None

def main():
    data=json.loads(SRC.read_text(encoding="utf-8")); con=sqlite3.connect(DB); unmatched=[]; n=0
    for r in data["matches"]:
        m=find_match(con,r)
        if not m: unmatched.append(f'{r["home"]} vs {r["away"]} {r["date"]}'); continue
        mid=m[0]; note="manual_screenshot_review_2026-09-20"
        con.execute("DELETE FROM review WHERE match_id=? AND error_reason LIKE 'manual_screenshot_review_%'",(mid,))
        con.execute("DELETE FROM prediction_snapshot WHERE match_id=? AND notes LIKE ?",(mid,note+"%"))
        cur=con.execute("""INSERT INTO prediction_snapshot
          (match_id,snapshot_time,data_cutoff,handicap,handicap_prediction,score_1,score_2,half_full,model_confidence,notes)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",(mid,data["reviewed_at"]+"T00:00:00",r["date"]+"T12:00:00",r["handicap"],r["pred"],r["score1"],r["score2"],None,r["confidence"]/100,note))
        con.execute("""INSERT INTO review
          (match_id,prediction_id,actual_result,handicap_actual,hit_status,error_type,error_reason,calibration_change,reviewed_at)
          VALUES(?,?,?,?,?,?,?,?,?)""",(mid,cur.lastrowid,r["actual"],r["actual_handicap"],"hit" if r["hit"] else "miss","none" if r["hit"] else "handicap_mapping","manual_screenshot_review_"+("hit" if r["hit"] else "miss"),"retain as confirmed sample" if r["hit"] else "review integer handicap calibration",data["reviewed_at"]))
        n+=1
    con.commit(); con.close(); print("manual_review_inserted="+str(n)); print("manual_review_unmatched="+str(len(unmatched)))
    if unmatched: print("\n".join(unmatched)); raise SystemExit(1)
if __name__=="__main__": main()
