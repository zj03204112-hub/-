#!/usr/bin/env python3
"""Apply the manually rechecked public match-page xG rows with strict match identity checks."""
import csv,json,re,unicodedata
from datetime import datetime,timezone
from pathlib import Path
R=Path("data/season_match_detail")
MAP=R/"j1_2026_match_id_mapping.csv"
CAN=R/"j1_2026_match_xg_free.csv"
MAN=R/"j1_2026_match_xg_manual_web_verifications.csv"
AUD=R/"j1_2026_match_xg_free_audit.json"
OUT=R/"j1_2026_match_xg_manual_web_verification_audit.json"
def read(p):
 with p.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
def write(p,rows,fields):
 with p.open("w",encoding="utf-8-sig",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)
def norm(v):
 v=unicodedata.normalize("NFKC",str(v or "")).casefold()
 return re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u9fff]+","",v)
def same_num(a,b):
 try:return abs(float(a)-float(b))<0.001
 except (ValueError,TypeError):return False
def main():
 mapping=read(MAP);canonical=read(CAN);manual=read(MAN);mb={r["internal_match_id"]:r for r in mapping};cb={r["internal_match_id"]:r for r in canonical}
 applied=[];rejected=[];discrepancies=[];now=datetime.now(timezone.utc).isoformat()
 valid_status={"verified_from_current_fetch","verified_from_archived_source_snapshot","verified_from_current_legacy_source_fetch","verified_from_manual_web_access"}
 for m in manual:
  mid=m["internal_match_id"];match=mb.get(mid);cur=cb.get(mid)
  if not match or not cur:
   rejected.append({"internal_match_id":mid,"reason":"missing_mapping_or_canonical_row"});continue
  checks={
   "date":m["match_date"]==match.get("match_date",""),
   "home_team":norm(m["home_team"])==norm(match.get("home_team_source","")),
   "away_team":norm(m["away_team"])==norm(match.get("away_team_source","")),
   "home_score":same_num(m["home_goals"],match.get("home_goals","")),
   "away_score":same_num(m["away_goals"],match.get("away_goals","")),
   "source_url":bool(m.get("source_url","").startswith("https://"))
  }
  if not all(checks.values()):
   rejected.append({"internal_match_id":mid,"reason":"strict_identity_check_failed","checks":checks});continue
  oldh,olda=cur.get("home_xg",""),cur.get("away_xg","")
  oldstatus=cur.get("source_validation_status","")
  if oldh and olda and oldstatus in valid_status and oldstatus!="verified_from_manual_web_access" and cur.get("source_name") not in ("",m["source_name"]):
   rejected.append({"internal_match_id":mid,"reason":"preserved_existing_verified_different_provider","existing_source":cur.get("source_name"),"manual_source":m["source_name"]});continue
  vh,va=m["verified_home_xg"],m["verified_away_xg"]
  archived_h,archived_a=m.get("home_xg",""),m.get("away_xg","")
  diff=not (same_num(vh,archived_h) and same_num(va,archived_a))
  if diff:discrepancies.append({"internal_match_id":mid,"source_url":m["source_url"],"archived_xg":f"{archived_h}-{archived_a}","current_page_xg":f"{vh}-{va}"})
  cur.update({
   "internal_match_id":mid,"provider_event_id":match.get("provider_event_id",""),"match_date":match.get("match_date",""),
   "home_team":match.get("home_team_source",""),"away_team":match.get("away_team_source",""),
   "home_goals":match.get("home_goals",""),"away_goals":match.get("away_goals",""),
   "home_xg":vh,"away_xg":va,"home_xga":va,"away_xga":vh,
   "xg_status":"verified_free_public_match_page_manual","source_name":m["source_name"],"source_url":m["source_url"],
   "source_model_note":f'{m["source_name"]} provider-specific match xG manually rechecked from a public match-specific page on 2026-11-02; not official J.League xG. xGA columns are opponent-xG-equivalents, not independently published match-level xGA.',
   "source_validation_status":"verified_from_manual_web_access",
   "source_reverification_note":f'{m["verification_note"]} Manual page identity: {m["match_date"]} {m["home_team"]} {m["home_goals"]}-{m["away_goals"]} {m["away_team"]}.',
   "source_raw_row":f'manual public page check; archived xG={archived_h}-{archived_a}; current page xG={vh}-{va}',
   "retrieved_at_utc":now
  })
  applied.append({"internal_match_id":mid,"source_name":m["source_name"],"source_url":m["source_url"],"xg":f"{vh}-{va}","archived_xg":f"{archived_h}-{archived_a}","archived_value_changed":diff})
 fields=list(canonical[0].keys()) if canonical else []
 for k in ["source_validation_status","source_reverification_note","source_raw_row","retrieved_at_utc"]:
  if k not in fields:fields.append(k)
 write(CAN,canonical,fields)
 # Update the main xG audit without claiming independent match-level xGA.
 try:a=json.loads(AUD.read_text(encoding="utf-8"))
 except Exception:a={}
 valid=sum(bool(r.get("home_xg") and r.get("away_xg") and r.get("source_validation_status") in valid_status) for r in canonical)
 a.update({"generated_at_utc":now,"mapped_matches":len(mapping),"matches_with_xg":valid,"matches_missing_xg":len(mapping)-valid,
           "manual_web_verification_rows_in_file":len(manual),"manual_web_verification_rows_applied":len(applied),
           "manual_web_verification_rows_rejected":len(rejected),"manual_web_value_discrepancies":len(discrepancies),
           "manual_web_verification_audit_file":OUT.name,"independent_match_level_xga_verified":0,
           "xga_semantics":"home_xga/away_xga are opponent-xG-equivalents for rows with verified match xG; no separate independently published match-level xGA has been verified.",
           "no_imputation":True})
 AUD.write_text(json.dumps(a,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 report={"generated_at_utc":now,"mapped_matches":len(mapping),"manual_rows":len(manual),"applied_rows":len(applied),
         "rejected_rows":len(rejected),"value_discrepancies":discrepancies,"applied":applied,"rejected":rejected,
         "verification_policy":"Manual web-access verification is recorded only for match-specific public pages whose displayed date, teams, final score and xG pair were checked. Archived/current source discrepancies are retained, not silently overwritten.",
         "independent_match_level_xga_verified":0,"xga_note":"Opponent-xG-equivalent only; independent match-level xGA remains unverified."}
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({"applied":len(applied),"rejected":len(rejected),"value_discrepancies":len(discrepancies),"verified_total":valid,"missing_total":len(mapping)-valid},ensure_ascii=False))
if __name__=="__main__":main()
