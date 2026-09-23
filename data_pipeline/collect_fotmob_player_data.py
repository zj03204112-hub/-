import json, re, sqlite3, time, unicodedata
from datetime import datetime
from difflib import SequenceMatcher
import requests, os

DB="football_model_database.sqlite"; BASE="https://www.fotmob.com/api/data"
START="2026-01-01"; END="2026-09-20"; MAX_MATCHES=int(os.getenv("FOTMOB_MAX_MATCHES","10"))
ALIASES={"psg":"parissaintgermain","parissg":"parissaintgermain","bayernmunich":"bayernmunchen","intermilan":"inter","internazionale":"inter","manutd":"manchesterunited","manchesterutd":"manchesterunited","manchesterunitedfc":"manchesterunited","mancity":"mancity","manchestercity":"mancity","tottenhamhotspur":"tottenham","athleticbilbao":"athleticclub","borussiadortmund":"dortmund","borussiamonchengladbach":"monchengladbach"}
def norm(s): return re.sub(r"[^a-z0-9]","",unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower())
def key(s):
 n=norm(s)
 for suf in ("footballclub","fc","cf","calcio","1899"):
  if n.endswith(suf) and len(n)>len(suf)+4:n=n[:-len(suf)]
 return ALIASES.get(n,n)
def sim(a,b):
 a,b=key(a),key(b); return 1.0 if a==b or a in b or b in a else SequenceMatcher(None,a,b).ratio()
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36","Accept":"application/json,text/plain,*/*","Referer":"https://www.fotmob.com/"})
def get(path):
 err=""
 for i in range(3):
  try:
   r=S.get(BASE+path,timeout=20)
   if r.status_code==200 and "json" in r.headers.get("content-type","").lower(): return r.json(),r.status_code,""
   err=f"HTTP {r.status_code}: {r.text[:180].replace(chr(10),' ')}"
  except Exception as e: err=f"{type(e).__name__}: {e}"
  time.sleep(1.5*(i+1))
 return None,None,err
def players(detail,side):
    """Extract player rows from FotMob's current matchDetails lineup schema.
    Supports both the historical lineup.lineup[2] structure and newer nested
    starters/substitutes payloads. We deliberately keep this tolerant because
    FotMob has changed the JSON shape several times.
    """
    content=detail.get("content") or {}
    raw=((content.get("lineup") or {}).get("lineup"))
    if not raw:
        raw=(content.get("lineup") or {}).get("lineups")
    if not raw:
        return []

    teams=raw if isinstance(raw,list) else [raw]
    target_index=0 if side=="home" else 1
    team_obj=teams[target_index] if len(teams)>target_index and isinstance(teams[target_index],dict) else None

    # Some payloads use {players:[...]} while older payloads split players into
    # positional groups. Flatten recursively, retaining only actual player nodes.
    roots=[]
    if team_obj:
        for k in ("players","starters","startingPlayers","subs","substitutes","bench"):
            v=team_obj.get(k)
            if v: roots.append(v)
    else:
        roots.append(raw)

    found=[]
    seen=set()
    def walk(x, inherited_starter=None):
        if isinstance(x,list):
            for y in x: walk(y,inherited_starter)
            return
        if not isinstance(x,dict): return

        pl=x.get("player") if isinstance(x.get("player"),dict) else x
        pid=pl.get("id") or x.get("id") or x.get("playerId")
        name=pl.get("name") or pl.get("shortName") or x.get("name")
        if pid and name:
            stats=x.get("stats") or x.get("statistics") or pl.get("stats") or {}
            rating=x.get("rating")
            if isinstance(rating,dict):
                rating=rating.get("num") or rating.get("value")
            st=stats if isinstance(stats,dict) else {}
            def num(*ks):
                for k in ks:
                    v=st.get(k)
                    if v is None: v=x.get(k)
                    try:
                        if v is not None: return float(v)
                    except (TypeError,ValueError): pass
                return 0.0
            starter=inherited_starter
            if starter is None:
                starter=not bool(x.get("substitute") or x.get("isSubstitute"))
            row=(str(pid),name,x.get("position") or pl.get("position") or x.get("usualPosition"),
                 1 if starter else 0,num("minutesPlayed","minsPlayed","minutes"),
                 rating,num("goals"),num("assists","goalAssist"),
                 num("expectedGoals","xg"),num("expectedAssists","xa"),
                 num("totalShots","shots","totalScoringAtt"),num("keyPasses","keyPass"),
                 num("tackles"),num("interceptions"),num("clearances"))
            if str(pid) not in seen:
                seen.add(str(pid)); found.append(row)
            return

        for k,v in x.items():
            if k in ("players","starters","startingPlayers"):
                walk(v,True)
            elif k in ("subs","substitutes","bench"):
                walk(v,False)
            elif k in ("player","stats","statistics"):
                walk(v,inherited_starter)
            elif isinstance(v,(dict,list)):
                walk(v,inherited_starter)

    for root in roots: walk(root,None)
    return found

def main():
 con=sqlite3.connect(DB); rows=con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches WHERE kickoff>=? AND kickoff<=? ORDER BY kickoff",(START,END+"T23:59:59")).fetchall()
 by_date={}
 for mid,ko,h,a in rows: by_date.setdefault(ko[:10],[]).append((mid,h,a))
 scanned=candidates=mapped=prow=fail=0; errors={}
 for d in sorted(by_date):
  payload,status,err=get(f"/matches?date={d.replace('-','')}"); scanned+=1
  if payload is None:
   fail+=1; errors[str(status or "request")]=errors.get(str(status or "request"),0)+1
   if fail<=5: print(json.dumps({"fotmob_error_date":d,"status":status,"error":err},ensure_ascii=False),flush=True)
   continue
  fmatches=[m for lg in payload.get("leagues") or [] for m in lg.get("matches") or []]
  for fm in fmatches:
   fh=(fm.get("home") or {}).get("name",""); fa=(fm.get("away") or {}).get("name","")
   best=max((( (sim(fh,h)+sim(fa,a))/2,mid) for mid,h,a in by_date[d]),default=(0,None))
   if best[0]<.78: continue
   candidates+=1
   if mapped>=MAX_MATCHES: break
   fid=str(fm.get("id")); detail,ds,de=get(f"/matchDetails?matchId={fid}")
   if detail is None:
    fail+=1
    if fail<=10: print(json.dumps({"fotmob_detail_error":fid,"status":ds,"error":de},ensure_ascii=False),flush=True)
    continue
   mid=best[1]; con.execute("INSERT OR REPLACE INTO provider_event_map(match_id,provider,event_id,observed_at) VALUES(?,?,?,?)",(mid,"fotmob",fid,datetime.utcnow().isoformat(timespec="seconds")))
   ins=0
   for side in ("home","away"):
    for p in players(detail,side):
     con.execute("""INSERT OR REPLACE INTO player_match_stats(match_id,team_side,player_id,player_name,position,starter,minutes_played,rating,goals,assists,xg,xa,shots,key_passes,tackles,interceptions,clearances,data_source,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(mid,side,*p,"FotMob public matchDetails",datetime.utcnow().isoformat(timespec="seconds"))); prow+=1; ins+=1
   if ins:mapped+=1
   con.commit()
   if mapped%10==0: print(json.dumps({"dates":f"{scanned}/{len(by_date)}","candidates":candidates,"mapped_matches":mapped,"player_rows":prow,"failures":fail},ensure_ascii=False),flush=True)
   time.sleep(.15)
  if mapped>=MAX_MATCHES: break
 con.close(); result={"dates_scanned":scanned,"candidate_matches":candidates,"mapped_matches":mapped,"player_rows":prow,"failures":fail,"errors":errors,"max_matches":MAX_MATCHES}; print(json.dumps(result,ensure_ascii=False),flush=True)
 if mapped==0 or prow==0: raise SystemExit("FOTMOB_PLAYER_DATA_EMPTY")
if __name__=="__main__":main()
