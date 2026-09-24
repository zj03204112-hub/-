import json, re, sqlite3, time, unicodedata
from datetime import datetime
from difflib import SequenceMatcher
import requests, os

DB="football_model_database.sqlite"; BASE="https://www.fotmob.com/api/data"
START="2026-01-01"; END="2026-09-20"; MAX_MATCHES=int(os.getenv("FOTMOB_MAX_MATCHES","300"))
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
    lineup=content.get("lineup") or {}
    # FotMob has used several schemas: lineup.home/away, lineup.lineup,
    # lineup.lineups, and team-keyed nested objects. Prefer the explicit side.
    side_obj=(lineup.get(f"{side}Team") or lineup.get(side)) if isinstance(lineup,dict) else None
    if isinstance(side_obj,dict):
        team_obj=side_obj
    else:
        raw=lineup.get("lineup") if isinstance(lineup,dict) else None
        if not raw:
            raw=lineup.get("lineups") if isinstance(lineup,dict) else None
        if not raw:
            raw=side_obj
        if not raw:
            return []
        teams=raw if isinstance(raw,list) else [raw]
        target_index=0 if side=="home" else 1
        team_obj=teams[target_index] if len(teams)>target_index and isinstance(teams[target_index],dict) else None

    # Some payloads store minutes in a nested match-stat object rather than
    # the player performance object. Keep a fallback so played players are not
    # misclassified as unused substitutes.

    roots=[]
    if team_obj:
        for k in ("players","starters","startingPlayers","subs","substitutes","bench"):
            v=team_obj.get(k)
            if v: roots.append(v)
    else:
        roots.append(raw)

    found=[]
    seen=set()
    def scalar(v):
        if isinstance(v, (int,float)): return float(v)
        if isinstance(v, dict):
            for k in ("value","num","number","raw","displayValue"):
                z=v.get(k)
                if isinstance(z,(int,float)): return float(z)
                if isinstance(z,str):
                    m=re.search(r"-?\d+(?:\.\d+)?",z.replace(",",""))
                    if m:
                        try: return float(m.group(0))
                        except ValueError: pass
        if isinstance(v,str):
            m=re.search(r"-?\d+(?:\.\d+)?",v.replace(",",""))
            if m:
                try: return float(m.group(0))
                except ValueError: pass
        return None
    def walk(x, inherited_starter=None):
        if isinstance(x,list):
            for y in x: walk(y,inherited_starter)
            return
        if not isinstance(x,dict): return

        pl=x.get("player") if isinstance(x.get("player"),dict) else x
        pid=pl.get("id") or x.get("id") or x.get("playerId")
        name=pl.get("name") or pl.get("shortName") or x.get("name")
        if pid and name:
            stats=x.get("stats") or x.get("statistics") or x.get("performance") or pl.get("stats") or pl.get("performance") or {}
            rating=x.get("rating") or (stats.get("rating") if isinstance(stats,dict) else None) or (pl.get("rating") if isinstance(pl,dict) else None)
            rating=scalar(rating)
            st=stats if isinstance(stats,dict) else {}
            def num(*ks):
                for k in ks:
                    v=st.get(k)
                    if v is None: v=x.get(k)
                    z=scalar(v)
                    if z is not None: return z
                return 0.0
            starter=inherited_starter
            if starter is None:
                starter=not bool(x.get("substitute") or x.get("isSubstitute"))
            mins=num("minutesPlayed","minsPlayed","minutes","minutes_played","minutesPlayedTotal")
            events=(st.get("substitutionEvents") if isinstance(st,dict) else None) or ((pl.get("performance") or {}).get("substitutionEvents") if isinstance(pl,dict) else None) or []
            if not mins and isinstance(events,list):
                for ev in events:
                    if not isinstance(ev,dict): continue
                    t=scalar(ev.get("time"))
                    typ=str(ev.get("type") or "")
                    if t is None: continue
                    if "subOut" in typ:
                        mins=max(0.0,float(t)); break
                    if "subIn" in typ:
                        mins=max(0.0,90.0-float(t)); break
            if not mins and starter and rating is not None:
                mins=90.0
            row=(str(pid),name,x.get("position") or pl.get("position") or x.get("usualPosition"),
                 1 if starter else 0,mins,rating,num("goals"),num("assists","goalAssist"),
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
 con=sqlite3.connect(DB); rows=con.execute("SELECT match_id,kickoff,home_team,away_team FROM matches WHERE kickoff>=? AND kickoff<=? ORDER BY kickoff DESC",(START,END+"T23:59:59")).fetchall()
 by_date={}
 # For a bounded validation run, only inspect the newest dates until we have a
 # small candidate pool. This prevents a 10-match test from scanning 260+ days.
 target_pool=max(MAX_MATCHES*5,1000)
 selected=[]
 for r in rows:
  selected.append(r)
  if len(selected)>=target_pool: break
 for mid,ko,h,a in selected: by_date.setdefault(ko[:10],[]).append((mid,h,a))
 scanned=candidates=mapped=prow=fail=0; errors={}
 for d in sorted(by_date, reverse=True):
  payload,status,err=get(f"/matches?date={d.replace('-','')}"); scanned+=1
  if payload is None:
   fail+=1; errors[str(status or "request")]=errors.get(str(status or "request"),0)+1
   if fail<=5: print(json.dumps({"fotmob_error_date":d,"status":status,"error":err},ensure_ascii=False),flush=True)
   continue
  fmatches=[m for lg in payload.get("leagues") or [] for m in lg.get("matches") or []]
  # Match each FotMob event to a unique local match on the same date.
  # The previous implementation could reuse the same local match_id for many
  # provider events because it independently selected the best match each time.
  used_local=set()
  used_fotmob=set()
  for fm in fmatches:
   fid=str(fm.get("id"))
   if not fid or fid in used_fotmob: continue
   fh=(fm.get("home") or {}).get("name",""); fa=(fm.get("away") or {}).get("name","")
   ranked=sorted((( (sim(fh,h)+sim(fa,a))/2,mid,h,a) for mid,h,a in by_date[d] if mid not in used_local), reverse=True)
   best=ranked[0] if ranked else (0,None,"","")
   if best[0]<.78: continue
   candidates+=1
   if mapped>=MAX_MATCHES: break
   fid=str(fm.get("id")); detail,ds,de=get(f"/matchDetails?matchId={fid}")
   if detail is None:
    fail+=1
    if fail<=10: print(json.dumps({"fotmob_detail_error":fid,"status":ds,"error":de},ensure_ascii=False),flush=True)
    continue
   mid=best[1]; used_local.add(mid); used_fotmob.add(fid); con.execute("INSERT OR REPLACE INTO provider_event_map(match_id,provider,event_id,observed_at) VALUES(?,?,?,?)",(mid,"fotmob",fid,datetime.utcnow().isoformat(timespec="seconds")))
   ins=0
   for side in ("home","away"):
    for p in players(detail,side):
     con.execute("""INSERT OR REPLACE INTO player_match_stats(match_id,team_side,player_id,player_name,position,starter,minutes_played,rating,goals,assists,xg,xa,shots,key_passes,tackles,interceptions,clearances,data_source,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(mid,side,*p,"FotMob public matchDetails",datetime.utcnow().isoformat(timespec="seconds"))); prow+=1; ins+=1
   if not ins:
    lineup=(detail.get("content") or {}).get("lineup") or {}
    if not getattr(main, "_debug_dumped", False):
     def shape(x, depth=0):
      if depth > 3 or not isinstance(x, dict):
       return None
      out={}
      for k,v in list(x.items())[:20]:
       if isinstance(v,dict):
        out[k]={"__keys__":list(v.keys())[:30], **(shape(v,depth+1) or {})}
       elif isinstance(v,list):
        out[k]={"__list_len__":len(v),"__item_keys__":list(v[0].keys())[:30] if v and isinstance(v[0],dict) else None}
       else:
        out[k]=type(v).__name__
      return out
     print(json.dumps({"fotmob_empty_players":fid,"lineup_shape":shape(lineup)},ensure_ascii=False),flush=True)
     main._debug_dumped=True
   if ins:
    mapped+=1
    if not getattr(main, "_sample_player_dumped", False):
     # Diagnostic: expose one real player node shape so minutes can be mapped
     # from FotMob's current payload instead of guessing field aliases.
     lineup=(detail.get("content") or {}).get("lineup") or {}
     sample=[]
     def collect_nodes(x):
      if len(sample)>=3: return
      if isinstance(x,list):
       for y in x: collect_nodes(y)
      elif isinstance(x,dict):
       if (x.get("id") or x.get("playerId")) and (x.get("name") or x.get("shortName")):
        sample.append(x)
       for v in x.values():
        if isinstance(v,(dict,list)): collect_nodes(v)
     collect_nodes(lineup)
     print(json.dumps({"fotmob_player_node_sample":sample},ensure_ascii=False)[:12000],flush=True)
     main._sample_player_dumped=True
   con.commit()
   if mapped%10==0 or not ins: print(json.dumps({"dates":f"{scanned}/{len(by_date)}","candidates":candidates,"mapped_matches":mapped,"player_rows":prow,"failures":fail},ensure_ascii=False),flush=True)
   time.sleep(.15)
  if mapped>=MAX_MATCHES: break
 con.close(); result={"dates_scanned":scanned,"candidate_matches":candidates,"mapped_matches":mapped,"player_rows":prow,"failures":fail,"errors":errors,"max_matches":MAX_MATCHES}; print(json.dumps(result,ensure_ascii=False),flush=True)
 if mapped==0 or prow==0: raise SystemExit("FOTMOB_PLAYER_DATA_EMPTY")
if __name__=="__main__":main()
