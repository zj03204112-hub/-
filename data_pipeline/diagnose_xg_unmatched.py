#!/usr/bin/env python3
"""Conservative season-aware diagnosis of unmatched Understat fixtures."""
import csv, difflib, json, os, re, sqlite3, unicodedata
from datetime import datetime, timezone

DB=os.getenv("FOOTBALL_DB","football_model_database.sqlite")
CSV_PATH=os.getenv("XG_UNMATCHED_CSV","data/xg_unmatched_diagnosis.csv")
REPORT_PATH=os.getenv("XG_UNMATCHED_AUDIT","data/xg_unmatched_diagnosis_summary.json")
LEAGUES={"EPL":"EPL","La_Liga":"LALIGA","Bundesliga":"BUNDESLIGA","Serie_A":"SERIEA","Ligue_1":"LIGUE1"}

def norm(v):
 s=unicodedata.normalize("NFKD",str(v or "")).encode("ascii","ignore").decode().lower()
 s=re.sub(r"\b(fc|afc|cf|sc|club|calcio|football|the)\b"," ",s)
 return re.sub(r"[^a-z0-9]+","",s)
def dt(v):
 try:
  d=datetime.fromisoformat(str(v).replace("Z","+00:00"))
  return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
 except Exception: return None
def season_ok(y,label):
 try: y=int(y)
 except Exception: return False
 return str(label or "").strip() in (f"{y}/{str(y+1)[-2:]}",f"{y}/{y+1}",str(y))

def main():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 db=[]
 for r in c.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,m.season_id,
  cp.competition_code,s.season_label FROM matches m
  LEFT JOIN competitions cp ON cp.competition_id=m.competition_id
  LEFT JOIN seasons s ON s.season_id=m.season_id"""):
  d=dt(r["kickoff"])
  if d: db.append({"id":str(r["match_id"]),"d":d,"h":str(r["home_team"]),"a":str(r["away_team"]),
   "hn":norm(r["home_team"]),"an":norm(r["away_team"]),"league":r["competition_code"],"sid":r["season_id"],"sl":r["season_label"]})
 seasons={}
 for r in c.execute("""SELECT s.competition_id,cp.competition_code,s.season_label,s.season_id,
  COUNT(m.match_id) n FROM seasons s JOIN competitions cp ON cp.competition_id=s.competition_id
  LEFT JOIN matches m ON m.season_id=s.season_id GROUP BY s.season_id"""):
  seasons.setdefault(r["competition_code"],[]).append(dict(r))
 c.close()
 with open(CSV_PATH,newline="",encoding="utf-8-sig") as f: srcrows=list(csv.DictReader(f))
 out=[]; counts={}
 for s in srcrows:
  league=LEAGUES.get(s.get("source_league"),""); kick=dt(s.get("kickoff_utc"))
  sh,sa=norm(s.get("home_team_raw")),norm(s.get("away_team_raw"))
  ss=[x for x in seasons.get(league,[]) if season_ok(s.get("season"),x["season_label"])]
  ids={x["season_id"] for x in ss}
  pool=[m for m in db if m["league"]==league and m["sid"] in ids]
  cand=None
  if not ss or not pool:
   reason,status,note="league_season_coverage_absent","coverage_gap_needs_verification","No matching league-season rows in DB"
  else:
   exact=[m for m in pool if m["hn"]==sh and m["an"]==sa]
   if exact:
    exact.sort(key=lambda m:abs((m["d"]-kick).total_seconds()) if kick else 1e99); cand=exact[0]
    hours=abs((cand["d"]-kick).total_seconds())/3600 if kick else None
    if hours is not None and hours<=36:
     reason,status,note="exact_pair_within_window_unexpected_unmatched","needs_code_path_review","Exact pair and date within 36h"
    elif hours is not None and hours<=45*24:
     reason,status,note="date_difference_same_league_season","candidate_unverified",f"Same-season pair; date gap {hours:.1f}h"
    else:
     reason,status,note="same_pair_date_conflict_or_duplicate","manual_review_required","Same-season pair but date gap exceeds 45 days"
   else:
    fuzzy=[]
    for m in pool:
     if not kick or abs((m["d"]-kick).total_seconds())>10*86400: continue
     hs=difflib.SequenceMatcher(None,sh,m["hn"]).ratio() if sh else 0
     aw=difflib.SequenceMatcher(None,sa,m["an"]).ratio() if sa else 0
     if hs>=.72 and aw>=.72: fuzzy.append((hs+aw,abs((m["d"]-kick).total_seconds()),m,hs,aw))
    if fuzzy:
     fuzzy.sort(key=lambda x:(-x[0],x[1])); _,_,cand,hs,aw=fuzzy[0]
     reason,status,note="team_name_difference_same_league_season","candidate_unverified",f"Same season/date vicinity; similarity {hs:.2f}/{aw:.2f}"
    else:
     cross=[m for m in db if m["league"]==league and m["hn"]==sh and m["an"]==sa]
     if cross:
      cross.sort(key=lambda m:abs((m["d"]-kick).total_seconds()) if kick else 1e99); cand=cross[0]
      reason,status,note="exact_pair_only_in_other_season","season_conflict_manual_review","Pair exists but not in source season"
     else:
      reason,status,note="likely_missing_match_in_covered_league_season","manual_review_required","Season exists, no plausible same-season candidate; verify before calling missing"
  row=dict(s)
  row.update({"candidate_db_match_id":cand["id"] if cand else "",
   "candidate_db_home_team":cand["h"] if cand else "","candidate_db_away_team":cand["a"] if cand else "",
   "candidate_db_kickoff_utc":cand["d"].isoformat() if cand else "",
   "reason_category":reason,"verification_status":status,"diagnosis_note":note})
  out.append(row); counts[reason]=counts.get(reason,0)+1
 fields=list(out[0]) if out else []
 with open(CSV_PATH,"w",newline="",encoding="utf-8-sig") as f:
  w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
 report={"csv":CSV_PATH,"rows":len(out),"unique_source_match_ids":len(set(x.get("source_match_id","") for x in out)),
  "reason_counts":counts,"counts_sum_matches_rows":sum(counts.values())==len(out),
  "note":"Candidates are suggestions only. No row is automatically joined; likely missing still requires verification.",
  "generated_at_utc":datetime.now(timezone.utc).isoformat(timespec="seconds")}
 with open(REPORT_PATH,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
 print(json.dumps(report,ensure_ascii=False,indent=2))
 if len(out)!=len(srcrows) or len(set(x.get("source_match_id","") for x in out))!=len(out): raise SystemExit("CSV_VALIDATION_FAILED")
if __name__=="__main__": main()
