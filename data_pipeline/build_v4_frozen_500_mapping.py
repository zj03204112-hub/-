import csv, json, re, sqlite3, unicodedata
from difflib import SequenceMatcher
from collections import Counter

CSV_PATH = "model_validation/500_match_base_batch01.csv"
DB = "football_model_database.sqlite"
OUT = "model_validation/500_match_to_db_match_id_mapping.csv"

LEAGUE_CODE = {
    "英超":"EPL", "EPL":"EPL", "Premier League":"EPL",
    "西甲":"LALIGA", "La Liga":"LALIGA", "LaLiga":"LALIGA",
    "德甲":"BUNDESLIGA", "Bundesliga":"BUNDESLIGA",
    "意甲":"SERIEA", "Serie A":"SERIEA",
    "法甲":"LIGUE1", "Ligue 1":"LIGUE1",
    "韩职":"KLEAGUE1", "K League 1":"KLEAGUE1", "K1":"KLEAGUE1",
    "日职J1":"J1", "J1 League":"J1", "J1":"J1",
}

TEAM_ALIAS = {
"切尔西":"Chelsea","布莱顿":"Brighton","狼队":"Wolves","伯恩茅斯":"Bournemouth","曼联":"Man United","曼城":"Man City","纽卡斯尔联":"Newcastle","热刺":"Tottenham","诺丁汉森林":"Nott'm Forest","阿斯顿维拉":"Aston Villa","考文垂":"Coventry","皇家奥维耶多":"Oviedo","毕尔巴鄂竞技":"Ath Bilbao","皇家社会":"Sociedad","马德里竞技":"Ath Madrid","皇家贝蒂斯":"Betis","皇家马德里":"Real Madrid","巴塞罗那":"Barcelona","皇家贝蒂斯":"Betis","阿拉维斯":"Alaves","塞尔塔":"Celta","塞维利亚":"Sevilla","比利亚雷亚尔":"Villarreal","赫塔费":"Getafe","马略卡":"Mallorca","莱万特":"Levante","西班牙人":"Espanol","瓦伦西亚":"Valencia","赫罗纳":"Girona","奥萨苏纳":"Osasuna","南特":"Nantes","雷恩":"Rennes","巴黎圣日耳曼":"Paris SG","里昂":"Lyon","摩纳哥":"Monaco","里尔":"Lille","斯特拉斯堡":"Strasbourg","图卢兹":"Toulouse","巴黎FC":"Paris FC","勒阿弗尔":"Le Havre","昂热":"Angers","特鲁瓦":"Troyes","勒芒":"Le Mans","洛里昂":"Lorient","桑德兰":"Sunderland","富勒姆":"Fulham","阿斯顿维拉":"Aston Villa","阿森纳":"Arsenal","曼联":"Man United","伊普斯维奇":"Ipswich Town","利物浦":"Liverpool","诺丁汉森林":"Nott'm Forest","托特纳姆热刺":"Tottenham","热刺":"Tottenham","纽卡斯尔联":"Newcastle","伯恩茅斯":"Bournemouth","埃弗顿":"Everton","水晶宫":"Crystal Palace","曼城":"Man City","布伦特福德":"Brentford","考文垂":"Coventry","西汉姆联":"West Ham United","伯恩利":"Burnley","利兹联":"Leeds","狼队":"Wolves","赫尔城":"Hull City",
"RB莱比锡":"RB Leipzig","云达不来梅":"Werder Bremen","云达不莱梅":"Werder Bremen","勒沃库森":"Bayer Leverkusen","圣保利":"St Pauli","埃尔沃斯贝格":"SV Elversberg","多特蒙德":"Borussia Dortmund","奥格斯堡":"Augsburg","帕德博恩":"Paderborn","弗赖堡":"SC Freiburg","拜仁慕尼黑":"Bayern Munich","斯图加特":"VfB Stuttgart","柏林联合":"Union Berlin","汉堡":"Hamburger SV","沃尔夫斯堡":"Wolfsburg","沙尔克04":"Schalke 04","法兰克福":"Eintracht Frankfurt","海登海姆":"Heidenheim","科隆":"FC Cologne","美因茨":"Mainz 05","门兴格拉德巴赫":"Borussia Mönchengladbach","霍芬海姆":"TSG Hoffenheim",
"AC米兰":"AC Milan","乌迪内斯":"Udinese","亚特兰大":"Atalanta","佛罗伦萨":"Fiorentina","博洛尼亚":"Bologna","卡利亚里":"Cagliari","国际米兰":"Inter Milan","威尼斯":"Venezia","尤文图斯":"Juventus","帕尔马":"Parma","弗罗西诺内":"Frosinone","拉齐奥":"Lazio","热那亚":"Genoa","科莫":"Como","罗马":"Roma","莱切":"Lecce","萨索洛":"Sassuolo","蒙扎":"Monza","那不勒斯":"Napoli","都灵":"Torino",
"勒芒":"Le Mans","勒阿弗尔":"Le Havre","南特":"Nantes","图卢兹":"Toulouse","尼斯":"Nice","巴黎FC":"Paris FC","巴黎圣日耳曼":"Paris Saint-Germain","布雷斯特":"Brest","摩纳哥":"Monaco","斯特拉斯堡":"Strasbourg","昂热":"Angers","朗斯":"Lens","欧塞尔":"Auxerre","洛里昂":"Lorient","特鲁瓦":"Troyes","里尔":"Lille","里昂":"Lyon","雷恩":"Rennes","马赛":"Marseille",
"埃尔切":"Elche","塞尔塔":"Celta","塞维利亚":"Sevilla","奥萨苏纳":"Osasuna","巴列卡诺":"Rayo Vallecano","巴塞罗那":"Barcelona","拉科鲁尼亚":"Deportivo La Coruna","桑坦德竞技":"Racing Santander","比利亚雷亚尔":"Villarreal","毕尔巴鄂竞技":"Ath Bilbao","瓦伦西亚":"Valencia","皇家奥维耶多":"Oviedo","皇家社会":"Sociedad","皇家贝蒂斯":"Betis","皇家马德里":"Real Madrid","莱万特":"Levante","西班牙人":"Espanol","赫塔费":"Getafe","赫罗纳":"Girona","阿拉维斯":"Alaves","马德里竞技":"Ath Madrid","马拉加":"Malaga","马略卡":"Mallorca",
"강원":"Gangwon FC","광주":"Gwangju FC","김천":"Gimcheon Sangmu","대전":"Daejeon Hana Citizen","부천":"Bucheon FC 1995","서울":"FC Seoul","안양":"FC Anyang","울산":"Ulsan HD","인천":"Incheon United","전북":"Jeonbuk Hyundai Motors","제주":"Jeju SK","포항":"Pohang Steelers",
"FC东京":"FC Tokyo","东京绿茵":"Tokyo Verdy","京都不死鸟":"Kyoto Sanga","冈山绿雉":"Fagiano Okayama","千叶市原":"JEF United Chiba","名古屋鲸八":"Nagoya Grampus","大阪樱花":"Cerezo Osaka","大阪钢巴":"Gamba Osaka","川崎前锋":"Kawasaki Frontale","广岛三箭":"Sanfrecce Hiroshima","柏太阳神":"Kashiwa Reysol","横滨水手":"Yokohama F. Marinos","水户蜀葵":"Mito HollyHock","浦和红钻":"Urawa Reds","清水心跳":"Shimizu S-Pulse","町田泽维亚":"FC Machida Zelvia","神户胜利船":"Vissel Kobe","福冈黄蜂":"Avispa Fukuoka","长崎成功丸":"V-Varen Nagasaki","鹿岛鹿角":"Kashima Antlers",
"横浜FM":"Yokohama F. Marinos","鹿島":"Kashima Antlers","Ｇ大阪":"Gamba Osaka","浦和":"Urawa Reds","Ｃ大阪":"Cerezo Osaka","岡山":"Fagiano Okayama","名古屋":"Nagoya Grampus","清水":"Shimizu S-Pulse","福岡":"Avispa Fukuoka","神戸":"Vissel Kobe","柏":"Kashiwa Reysol","水戸":"Mito HollyHock","FC東京":"FC Tokyo","町田":"FC Machida Zelvia","広島":"Sanfrecce Hiroshima","千葉":"JEF United Chiba","東京Ｖ":"Tokyo Verdy","川崎Ｆ":"Kawasaki Frontale","長崎":"V-Varen Nagasaki","京都":"Kyoto Sanga"
}
def team_key(s):
    return norm_team(TEAM_ALIAS.get((s or "").strip(), s))

def similarity(a,b):
    return SequenceMatcher(None, norm_team(a), norm_team(b)).ratio()

def norm_team(s):
    s=(s or "").strip().lower()
    s="".join(ch for ch in unicodedata.normalize("NFKD",s) if not unicodedata.combining(ch))
    for token in ("football club","footballclub","fc","cf","afc","sc","fk","ac","calcio","club","足球俱乐部"):
        s=s.replace(token,"")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+","",s)

con=sqlite3.connect(DB)
schema=con.execute("PRAGMA table_info(matches)").fetchall()
cols=[r[1] for r in schema]
required={"match_id","competition_id","kickoff","home_team","away_team"}
missing=required-set(cols)
if missing:
    raise RuntimeError(f"MATCHES_SCHEMA_MISSING {sorted(missing)}")

db_rows=con.execute("""
SELECT m.match_id,m.competition_id,c.competition_code,c.competition_name,
       m.kickoff,m.home_team,m.away_team,
       r.ft_home,r.ft_away
FROM matches m
JOIN competitions c ON c.competition_id=m.competition_id
JOIN results r ON r.match_id=m.match_id
WHERE m.status='finished'
  AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
""").fetchall()

idx={}
for x in db_rows:
    key=(x[2],x[4][:10],team_key(x[5]),team_key(x[6]),x[7],x[8])
    idx.setdefault(key,[]).append(x)

out=[]
used_match_ids=set()
with open(CSV_PATH,encoding="utf-8-sig",newline="") as f:
    src=list(csv.DictReader(f))

for i,r in enumerate(src,1):
    league=r["league"].strip()
    code=LEAGUE_CODE.get(league)
    score=(int(r["home_score"]),int(r["away_score"]))
    key=(code,r["date"],team_key(r["home"]),team_key(r["away"]),score[0],score[1])
    cand=[x for x in idx.get(key,[]) if x[0] not in used_match_ids]
    date_team_pool=[x for x in db_rows if x[2]==code and x[4][:10]==r["date"] and team_key(x[5])==team_key(r["home"]) and team_key(x[6])==team_key(r["away"])]
    date_league_pool=[x for x in db_rows if x[2]==code and x[4][:10]==r["date"]]
    match_method="exact"
    if len(cand)!=1:
        pool=[x for x in db_rows if x[0] not in used_match_ids and x[2]==code and x[4][:10]==r["date"] and x[7]==score[0] and x[8]==score[1]]
        scored=[]
        for x in pool:
            sh=similarity(TEAM_ALIAS.get(r["home"],r["home"]),x[5]); sa=similarity(TEAM_ALIAS.get(r["away"],r["away"]),x[6])
            scored.append((sh+sa,sh,sa,x))
        scored.sort(reverse=True,key=lambda z:z[0])
        if scored and scored[0][0] >= 1.25 and min(scored[0][1],scored[0][2]) >= 0.55 and (len(scored)==1 or scored[0][0]-scored[1][0] >= 0.08):
            cand=[scored[0][3]]
            match_method="fuzzy_league_date_score_team"
    status="unique" if len(cand)==1 else "unresolved" if len(cand)==0 else "ambiguous"
    x=cand[0] if len(cand)==1 else [None, None, code, None, None, None, None, None, None]
    if len(cand)==1:
        used_match_ids.add(cand[0][0])
    out.append({
        "sample_row":i,"date":r["date"],"league":league,"competition_code":code,
        "home":r["home"],"away":r["away"],"home_score":score[0],"away_score":score[1],
        "mapping_status":status,"candidate_count":len(cand),"date_team_candidate_count":len(date_team_pool),"date_team_scores":";".join(f"{x[7]}-{x[8]}:{x[0]}" for x in date_team_pool),"date_league_count":len(date_league_pool),"date_league_scores":";".join(f"{x[5]}-{x[6]} {x[7]}-{x[8]}" for x in date_league_pool),"match_method":match_method,
        "match_id":x[0] if len(cand)==1 else "",
        "db_kickoff":x[4] if len(cand)==1 else "",
        "db_home":x[5] if len(cand)==1 else "",
        "db_away":x[6] if len(cand)==1 else "",
        "db_home_score":x[7] if len(cand)==1 else "",
        "db_away_score":x[8] if len(cand)==1 else "",
    })

if len(out)!=500 or len({x["sample_row"] for x in out})!=500:
    raise RuntimeError(f"FROZEN_SAMPLE_INTEGRITY_FAILED rows={len(out)}")

fields=list(out[0])
with open(OUT,"w",encoding="utf-8",newline="") as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)

counts=Counter(x["mapping_status"] for x in out)
print(json.dumps({"rows":len(out),"status_counts":counts,"output":OUT},ensure_ascii=False))
print("DB_FINISHED_RESULTS", con.execute("SELECT COUNT(*) FROM matches m JOIN results r ON r.match_id=m.match_id WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL").fetchone()[0])
print("DB_DATE_RANGE", con.execute("SELECT MIN(kickoff),MAX(kickoff) FROM matches WHERE status='finished'").fetchone())
print("DB_COMPETITIONS", con.execute("SELECT c.competition_code,COUNT(*) FROM matches m JOIN competitions c ON c.competition_id=m.competition_id WHERE m.status='finished' GROUP BY c.competition_code ORDER BY c.competition_code").fetchall())
print("DB_SAMPLE", con.execute("SELECT m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away,c.competition_code FROM matches m JOIN results r ON r.match_id=m.match_id JOIN competitions c ON c.competition_id=m.competition_id WHERE m.status='finished' ORDER BY m.kickoff LIMIT 5").fetchall())

# strict-one-to-one-remap-2026-10-01-final-check
