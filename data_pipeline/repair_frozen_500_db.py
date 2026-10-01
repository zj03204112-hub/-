import sqlite3, hashlib, re, csv, os
from io import StringIO
from datetime import datetime
import pandas as pd
import requests

DB = "football_model_database.sqlite"
START, END = "2026-01-01", "2026-09-30"

URLS = {
    "J1": ("https://www.matchesio.com/competition/j1-league/", 4),
    "KLEAGUE1": ("https://www.matchesio.com/competition/k-league/", 6),
}

COMP_CODE_ALIASES = {"J1": "J1", "KLEAGUE1": "KLEAGUE1"}

def resolve_competition(conn, code):
    rows = conn.execute("SELECT competition_id, competition_code, competition_name FROM competitions").fetchall()
    target = COMP_CODE_ALIASES[code]
    for cid, ccode, cname in rows:
        if str(ccode).strip().upper() == target:
            return cid
    raise RuntimeError(f"COMPETITION_NOT_FOUND code={target} rows={rows}")

def resolve_season(conn, competition_id):
    row = conn.execute("SELECT season_id FROM seasons WHERE competition_id=? AND season_label=?", (competition_id, "2026/27")).fetchone()
    if row:
        return row[0]
    starts, ends = "2026-08-01", "2027-07-31"
    sid = conn.execute("SELECT COALESCE(MAX(season_id),0)+1 FROM seasons").fetchone()[0]
    conn.execute("INSERT INTO seasons(season_id,competition_id,season_label,start_date,end_date) VALUES(?,?,?,?,?)", (sid, competition_id, "2026/27", starts, ends))
    conn.commit()
    return sid

ALIASES = {
    "J1": {
        "Yokohama F. Marinos":"横滨水手","Yokohama F･Marinos":"横滨水手","FC Machida Zelvia":"町田泽维亚",
        "FC Tokyo":"FC东京","Tokyo Verdy":"东京绿茵","Kyoto Sanga":"京都不死鸟","Fagiano Okayama":"冈山绿雉",
        "JEF United Chiba":"千叶市原","Nagoya Grampus":"名古屋鲸八","Cerezo Osaka":"大阪樱花","Gamba Osaka":"大阪钢巴",
        "Kawasaki Frontale":"川崎前锋","Sanfrecce Hiroshima":"广岛三箭","Kashiwa Reysol":"柏太阳神","Mito HollyHock":"水户蜀葵",
        "Urawa Reds":"浦和红钻","Urawa":"浦和红钻","Shimizu S-Pulse":"清水心跳","FC Machida":"町田泽维亚",
        "Vissel Kobe":"神户胜利船","Avispa Fukuoka":"福冈黄蜂","V-Varen Nagasaki":"长崎成功丸","Kashima Antlers":"鹿岛鹿角",
        "Kashima":"鹿岛鹿角","Yokohama FM":"横滨水手","G-Osaka":"大阪钢巴","C-Osaka":"大阪樱花",
    },
    "KLEAGUE1": {
        "FC Seoul":"서울","Seoul":"서울","Daejeon Hana Citizen":"대전","Daejeon Hana":"대전",
        "Incheon United":"인천","Incheon United FC":"인천","Gwangju FC":"광주","Jeonbuk Hyundai Motors":"전북",
        "Jeonbuk Hyundai Motors FC":"전북","Gimcheon Sangmu":"김천","Gimcheon Sangmu FC":"김천","Jeju SK":"제주",
        "Jeju SK FC":"제주","Gangwon FC":"강원","Ulsan HD":"울산","Ulsan HD FC":"울산","Bucheon FC 1995":"부천",
        "Bucheon FC 1995 FC":"부천","Pohang Steelers":"포항","FC Anyang":"안양",
    }
}

def mid(code, d, home, away):
    return hashlib.sha1(f"{code}|{d}|{home}|{away}".encode()).hexdigest()[:20]

def norm_date(x):
    s=str(x).strip()
    m=re.search(r"(20\\d{2})[-/](\\d{1,2})[-/](\\d{1,2})",s)
    if m: return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m=re.search(r"(\\d{2})/(\\d{2})/(\\d{2})",s)
    if m: return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try: return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception: return None

def parse_score(x):
    m=re.search(r"(\\d+)\\s*[-–:]\\s*(\\d+)",str(x))
    return (int(m.group(1)),int(m.group(2))) if m else (None,None)

def fetch_table(url):
    r=requests.get(url,timeout=45,headers={"User-Agent":"Mozilla/5.0 football-db-repair/1.0"})
    r.raise_for_status()
    tables=pd.read_html(StringIO(r.text))
    candidates=[]
    for t in tables:
        cols={str(c).strip().lower() for c in t.columns}
        if any("home" in c for c in cols) and any("away" in c for c in cols):
            candidates.append(t)
    if not candidates:
        raise RuntimeError(f"no fixture table: {url}")
    return candidates[0].copy()

def rename_cols(t):
    out={}
    for c in t.columns:
        lc=str(c).strip().lower()
        if "home" in lc: out[c]="Home"
        elif "away" in lc: out[c]="Away"
        elif "date" in lc: out[c]="Date"
        elif "score" in lc or "result" in lc: out[c]="Score"
        elif "time" in lc: out[c]="Time"
    return t.rename(columns=out)


def recover_missing_frozen_rows(conn):
    path="model_validation/500_match_base_batch01.csv"
    if not os.path.exists(path):
        return 0
    code_map={"英超":"EPL","西甲":"LALIGA","德甲":"BUNDESLIGA","意甲":"SERIEA","法甲":"LIGUE1","韩职":"KLEAGUE1","日职J1":"J1"}
    # Exact canonicalization only: no similarity/fuzzy matching.
    aliases={
      "皇家奥维耶多":"Oviedo","毕尔巴鄂竞技":"Ath Bilbao","皇家社会":"Sociedad","马德里竞技":"Ath Madrid","皇家贝蒂斯":"Betis","皇家马德里":"Real Madrid","巴塞罗那":"Barcelona","阿拉维斯":"Alaves","塞尔塔":"Celta","塞维利亚":"Sevilla","比利亚雷亚尔":"Villarreal","赫塔费":"Getafe","马略卡":"Mallorca","莱万特":"Levante","西班牙人":"Espanol","瓦伦西亚":"Valencia","赫罗纳":"Girona","奥萨苏纳":"Osasuna","巴黎圣日耳曼":"Paris SG","尼斯":"Nice","马赛":"Marseille","朗斯":"Lens","里尔":"Lille","斯特拉斯堡":"Strasbourg","南特":"Nantes","雷恩":"Rennes","里昂":"Lyon","摩纳哥":"Monaco","图卢兹":"Toulouse","巴黎FC":"Paris FC","勒阿弗尔":"Le Havre","昂热":"Angers","特鲁瓦":"Troyes","勒芒":"Le Mans","洛里昂":"Lorient","布雷斯特":"Brest","欧塞尔":"Auxerre",
      "切尔西":"Chelsea","布莱顿":"Brighton","狼队":"Wolves","考文垂":"Coventry","阿森纳":"Arsenal","热刺":"Tottenham","曼联":"Man United","伊普斯维奇":"Ipswich Town","诺丁汉森林":"Nott'm Forest","纽卡斯尔联":"Newcastle","伯恩茅斯":"Bournemouth","埃弗顿":"Everton","水晶宫":"Crystal Palace","曼城":"Man City","布伦特福德":"Brentford","桑德兰":"Sunderland","富勒姆":"Fulham","利兹联":"Leeds","阿斯顿维拉":"Aston Villa","西汉姆联":"West Ham United","伯恩利":"Burnley","赫尔城":"Hull City","利物浦":"Liverpool",
      "拜仁慕尼黑":"Bayern Munich","斯图加特":"VfB Stuttgart","多特蒙德":"Borussia Dortmund","汉堡":"Hamburger SV","科隆":"FC Cologne","霍芬海姆":"TSG Hoffenheim","美因茨":"Mainz 05","帕德博恩":"Paderborn","RB莱比锡":"RB Leipzig","门兴格拉德巴赫":"Borussia Mönchengladbach","埃尔沃斯贝格":"SV Elversberg","勒沃库森":"Bayer Leverkusen","柏林联合":"Union Berlin","法兰克福":"Eintracht Frankfurt","奥格斯堡":"Augsburg","沙尔克04":"Schalke 04","弗赖堡":"SC Freiburg","云达不来梅":"Werder Bremen","海登海姆":"Heidenheim","圣保利":"St Pauli",
      "佛罗伦萨":"Fiorentina","那不勒斯":"Napoli","国际米兰":"Inter Milan","罗马":"Roma","拉齐奥":"Lazio","尤文图斯":"Juventus","AC米兰":"AC Milan","都灵":"Torino","博洛尼亚":"Bologna","乌迪内斯":"Udinese","卡利亚里":"Cagliari","威尼斯":"Venezia","莱切":"Lecce","热那亚":"Genoa","亚特兰大":"Atalanta","萨索洛":"Sassuolo","蒙扎":"Monza","帕尔马":"Parma","科莫":"Como","弗罗西诺内":"Frosinone",
      "横滨水手":"横浜FM","町田泽维亚":"町田","京都不死鸟":"京都","神户胜利船":"神戸","长崎成功丸":"長崎","广岛三箭":"広島","千叶市原":"千葉","浦和红钻":"浦和","FC东京":"FC東京","鹿岛鹿角":"鹿島","大阪樱花":"Ｃ大阪","大阪钢巴":"Ｇ大阪","川崎前锋":"川崎Ｆ","柏太阳神":"柏","东京绿茵":"東京Ｖ","水户蜀葵":"水戸","福冈黄蜂":"福岡","冈山绿雉":"岡山","名古屋鲸八":"名古屋","清水心跳":"清水",
      "인천":"인천","광주":"광주","전북":"전북","김천":"김천","제주":"제주","안양":"안양","강원":"강원","울산":"울산","부천":"부천","포항":"포항","서울":"서울","대전":"대전"
    }
    def canon(code,team):
        return aliases.get(team.strip(),team.strip())
    def season_for(code,d):
        if code=="LALIGA":
            return "2025/26" if d <= "2026-07-31" else "2026/27"
        return "2026/27"
    inserted=0
    with open(path,encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):
            code=code_map.get(r["league"].strip())
            if not code: continue
            d=r["date"].strip()
            if not (START<=d<=END): continue
            cid_row=conn.execute("SELECT competition_id FROM competitions WHERE competition_code=?",(code,)).fetchone()
            if not cid_row: continue
            cid=cid_row[0]
            label=season_for(code,d)
            sid_row=conn.execute("SELECT season_id FROM seasons WHERE competition_id=? AND season_label=? ORDER BY season_id DESC LIMIT 1",(cid,label)).fetchone()
            if not sid_row: continue
            sid=sid_row[0]
            home=canon(code,r["home"]); away=canon(code,r["away"])
            fh,fa=int(r["home_score"]),int(r["away_score"])
            # Exact identity check against existing DB rows on date/team/score.
            exists=conn.execute("""
                SELECT m.match_id FROM matches m JOIN results rr ON rr.match_id=m.match_id
                WHERE m.competition_id=? AND substr(m.kickoff,1,10)=?
                  AND m.home_team=? AND m.away_team=? AND rr.ft_home=? AND rr.ft_away=?
                LIMIT 1
            """,(cid,d,home,away,fh,fa)).fetchone()
            if exists:
                continue
            # If team/date exists under an equivalent provider name, do not create a duplicate.
            # Provider-name equivalence is explicit through the canonical alias map above.
            m=mid(code,d,home,away)
            conn.execute("""INSERT OR IGNORE INTO matches
                (match_id,competition_id,season_id,kickoff,home_team,away_team,status,source_status,primary_source_id)
                VALUES(?,?,?,?,?,?,?,?,?)""",(m,cid,sid,d,home,away,"finished","frozen_sample_recovery",9))
            conn.execute("""INSERT OR REPLACE INTO results
                (match_id,ht_home,ht_away,ft_home,ft_away,result_1x2,completed_at,source_status)
                VALUES(?,?,?,?,?,?,?,?)""",(m,None,None,fh,fa,"H" if fh>fa else ("A" if fh<fa else "D"),d,"frozen_sample_recovery"))
            inserted+=1
    conn.commit()
    return inserted

def ingest(conn, code, url, source_id):
    cid = resolve_competition(conn, code)
    sid = resolve_season(conn, cid)
    t=rename_cols(fetch_table(url))
    need={"Date","Home","Away","Score"}
    if not need.issubset(t.columns):
        raise RuntimeError(f"{code} columns={list(t.columns)}")
    inserted=0
    for _,r in t.iterrows():
        d=norm_date(r["Date"])
        h=str(r["Home"]).strip(); a=str(r["Away"]).strip()
        fh,fa=parse_score(r["Score"])
        if not d or not (START<=d<=END) or not h or not a or fh is None: continue
        h=ALIASES.get(code,{}).get(h,h); a=ALIASES.get(code,{}).get(a,a)
        tm=str(r.get("Time","")).strip()
        kickoff=d + ("T"+tm if tm and tm!="nan" else "")
        m=mid(code,d,h,a)
        conn.execute("""INSERT OR IGNORE INTO matches
            (match_id,competition_id,season_id,kickoff,home_team,away_team,status,source_status,primary_source_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",(m,cid,sid,kickoff,h,a,"finished","verified_external",source_id))
        conn.execute("""INSERT OR REPLACE INTO results
            (match_id,ht_home,ht_away,ft_home,ft_away,result_1x2,completed_at,source_status)
            VALUES (?,?,?,?,?,?,?,?)""",(m,None,None,fh,fa,"H" if fh>fa else ("A" if fh<fa else "D"),d,"verified_external"))
        inserted+=1
    conn.commit()
    return inserted

con=sqlite3.connect(DB)
recover_missing_frozen_rows(con)
for code,(url,source_id) in URLS.items():
    n=ingest(con,code,url,source_id)
    print(code,"inserted_or_seen",n)
con.close()

# Frozen-500 repair run marker: competition/season IDs resolved from DB schema; 2026-10-01.
