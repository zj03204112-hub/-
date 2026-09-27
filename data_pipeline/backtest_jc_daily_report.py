import json, os, re, sqlite3
import requests

DB = "football_model_database.sqlite"
OUT = os.environ.get("SITE_BACKTEST_OUT", "data/jc_daily_report_backtest_100.json")
N = int(os.environ.get("SITE_BACKTEST_N", "100"))
REPO = "chinjiaqing/jc-daily-report"
RAW = "https://raw.githubusercontent.com/chinjiaqing/jc-daily-report/main/"
API = f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"

def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()

def outcome(h, a):
    return "H" if h > a else "D" if h == a else "A"

def parse_cards(html, source_date):
    out = []
    # Current jc-daily-report pages use table rows rather than the older match-card structure.
    for b in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.S):
        if "onclick=" not in b or " vs " not in b or "tnum" not in b:
            continue
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", b, re.S)
        if len(cells) < 8:
            continue
        row_text = [clean(x) for x in cells]
        tm = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", " ".join(row_text))
        teams_m = re.search(r"([^<]{1,40})\s+vs\s+([^<]{1,40})", cells[1], re.S)
        score_m = re.search(r'<div[^>]*class="tnum text-base font-black[^>]*>\s*(\d+)\s*:\s*(\d+)\s*</div>', cells[7], re.S)
        if not tm or not teams_m or not score_m:
            continue
        home = clean(teams_m.group(1))
        away = clean(teams_m.group(2))
        # Remove handicap annotation from the home label, e.g. 塞伊奈(+1).
        home = re.sub(r"\s*\([^)]*\)\s*$", "", home).strip()
        sh, sa = int(score_m.group(1)), int(score_m.group(2))

        # Prediction-only fields: cells 0..6. Never read the final score/verification cells.
        pred_text = " | ".join(row_text[:7])
        euro_signal = ""
        m = re.search(r"走势\s*(主胜|客胜|平)", pred_text)
        if m:
            euro_signal = m.group(1)
        handicap_pick = ""
        m = re.search(r"买【([^】]+)】", pred_text)
        if m:
            handicap_pick = m.group(1)

        # Use the actual kickoff shown on the card, not the page filename date.
        kickoff = tm.group(1)
        actual = outcome(sh, sa)
        out.append({
            "date": kickoff[:10],
            "kickoff": kickoff[11:],
            "home": home,
            "away": away,
            "score": [sh, sa],
            "actual": actual,
            "euro_signal": euro_signal,
            "handicap_pick": handicap_pick
        })
    return out

def signal_set(signal):
    if signal == "主胜":
        return {"H"}
    if signal == "客胜":
        return {"A"}
    if signal == "平":
        return {"D"}
    return set()

def canonical(s):
    s = s.lower()
    return re.sub(r"[\s·・.。'’‘()（）\-—_/+]+", "", s)

def main():
    tree = requests.get(API, timeout=30).json()
    files = [x["path"] for x in tree.get("tree", []) if x["path"].startswith("daily/") and x["path"].endswith(".html")]
    all_rows = []
    for path in sorted(files):
        d = path.split("/")[-1].replace(".html", "")
        try:
            html = requests.get(RAW + path, timeout=30).text
            all_rows.extend(parse_cards(html, d))
        except Exception as e:
            print("FETCH_FAIL", path, str(e))

    all_rows = sorted(all_rows, key=lambda x: (x["date"], x["kickoff"]), reverse=True)
    rows = all_rows[:N]

    exact = [r for r in rows if signal_set(r["euro_signal"])]
    exact_correct = sum(next(iter(signal_set(r["euro_signal"]))) == r["actual"] for r in exact)

    # The source's explicit Asian-handicap recommendation is retained separately.
    handicap = [r for r in rows if r["handicap_pick"]]
    handicap_home = sum("让主胜" in r["handicap_pick"] for r in handicap)
    handicap_draw = sum("让平" in r["handicap_pick"] for r in handicap)
    handicap_away = sum("让客胜" in r["handicap_pick"] for r in handicap)

    con = sqlite3.connect(DB)
    local = con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
      FROM matches m JOIN results r ON r.match_id=m.match_id
      WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL""").fetchall()
    by_key = {}
    for m in local:
        dt = m[1][:16].replace("T", " ")
        by_key[(dt, canonical(m[2]), canonical(m[3]))] = m

    matched = []
    for r in rows:
        dt = r["date"] + " " + r["kickoff"][:5]
        m = by_key.get((dt, canonical(r["home"]), canonical(r["away"])))
        if m:
            matched.append((r, m))

    result = {
      "definition": f"{N}-match descriptive validation of historical jc-daily-report signals. Prediction fields are read only from the pre-result columns; final score is used only as evaluation target. No coefficient is fitted and V4 is not changed.",
      "source_repo": REPO,
      "source_url": "https://github.com/chinjiaqing/jc-daily-report",
      "n_source_cards": len(all_rows),
      "n": len(rows),
      "euro_direction_signal": {
          "n": len(exact),
          "correct": exact_correct,
          "accuracy": round(exact_correct / len(exact), 4) if exact else None
      },
      "handicap_signal_cards": len(handicap),
      "handicap_pick_counts": {
          "让主胜": handicap_home,
          "让平": handicap_draw,
          "让客胜": handicap_away
      },
      "local_db_overlap": len(matched),
      "warning": "This is a parser/coverage validation first. It does not represent V4+source blended-model accuracy until the same matches are linked to V4 predictions under a pre-declared fusion rule.",
      "sample": rows[:20],
      "matched": [{"source": r, "local": {"match_id": m[0], "kickoff": m[1], "home": m[2], "away": m[3], "actual": outcome(m[4], m[5])}} for r, m in matched]
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ("sample","matched")}, ensure_ascii=False))
    con.close()

if __name__ == "__main__":
    main()
