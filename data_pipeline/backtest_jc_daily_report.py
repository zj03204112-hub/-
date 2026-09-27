import json, os, re, sqlite3
import requests

DB = "football_model_database.sqlite"
OUT = os.environ.get("SITE_BACKTEST_OUT", "data/jc_daily_report_backtest_30_big5.json")
N = int(os.environ.get("SITE_BACKTEST_N", "30"))
REPO = "chinjiaqing/jc-daily-report"
RAW = "https://raw.githubusercontent.com/chinjiaqing/jc-daily-report/main/"
API = f"https://api.github.com/repos/{REPO}/git/trees/main?recursive=1"

def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()

def outcome(h, a):
    return "H" if h > a else "D" if h == a else "A"

def parse_cards(html, source_date):
    out = []
    # Current jc-daily-report pages use .match-card div blocks.
    for b in re.findall(r'<div\s+class="match-card[^"]*"[^>]*>(.*?)</div>\s*</div>\s*<div class="match-card', html, re.S):
        pass
    blocks = re.findall(r'<div class="match-card[^"]*"[^>]*>(.*?)(?=\n\s*<div class="match-card|\n</main>)', html, re.S)
    for b in blocks:
        league_m = re.search(r'<span class="font-bold">\s*([^<]+?)\s*</span>\s*<span class="font-mono">\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', b, re.S)\n        tm = league_m.group(2) if league_m else None\n        league = clean(league_m.group(1)) if league_m else ""
        teams_m = re.search(r'<span class="text-\[15px\] font-bold[^>]*>\s*(.*?)\s*<span[^>]*>vs</span>\s*(.*?)\s*</span>', b, re.S)
        score_m = re.search(r'<span class="tnum text-sm font-black[^>]*>\s*(\d+)\s*:\s*(\d+)\s*</span>', b, re.S)
        if not tm or not teams_m or not score_m:
            continue
        home = clean(teams_m.group(1))
        away = clean(teams_m.group(2))
        sh, sa = int(score_m.group(1)), int(score_m.group(2))
        row_text = clean(b)
        # Only prediction text before the final score is used for signals.
        pred_text = re.sub(r'\b\d+\s*:\s*\d+\b', ' ', row_text)
        euro_signal = ""
        signal_type = ""
        if "主不败" in pred_text:
            euro_signal, signal_type = "主不败", "double_chance_home"
        elif "客不败" in pred_text:
            euro_signal, signal_type = "客不败", "double_chance_away"
        elif re.search(r'\b主胜\b', pred_text):
            euro_signal, signal_type = "主胜", "home"
        elif re.search(r'\b客胜\b', pred_text):
            euro_signal, signal_type = "客胜", "away"
        elif re.search(r'\b平\b', pred_text):
            euro_signal, signal_type = "平", "draw"

        handicap_pick = ""
        m = re.search(r'买【([^】]+)】', pred_text)
        if m:
            handicap_pick = m.group(1)

        # The current repository HTML does not expose a per-match O/U line or
        # an explicit O/U side in the match card. Keep these fields null rather
        # than infer them from the final score.
        ou_line = None
        ou_pick = None

        actual = outcome(sh, sa)
        out.append({
            "league": league,\n            "date": tm[:10],
            "kickoff": tm[11:],
            "home": home,
            "away": away,
            "score": [sh, sa],
            "actual": actual,
            "euro_signal": euro_signal,
            "signal_type": signal_type,
            "handicap_pick": handicap_pick,
            "ou_line": ou_line,
            "ou_pick": ou_pick
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

    exact = [r for r in rows if r["euro_signal"]]
    exact_correct = 0
    for r in exact:
        if r["signal_type"] == "home":
            exact_correct += r["actual"] == "H"
        elif r["signal_type"] == "away":
            exact_correct += r["actual"] == "A"
        elif r["signal_type"] == "draw":
            exact_correct += r["actual"] == "D"
        elif r["signal_type"] == "double_chance_home":
            exact_correct += r["actual"] in ("H", "D")
        elif r["signal_type"] == "double_chance_away":
            exact_correct += r["actual"] in ("A", "D")

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
      "n_source_cards": len(all_rows),\n      "n_target_league_cards": len(target_rows),
      "n": len(rows),
      "direction_signal": {
          "n": len(exact),
          "correct": exact_correct,
          "accuracy": round(exact_correct / len(exact), 4) if exact else None,
          "note": "The source cards currently expose mainly 主不败/客不败 double-chance language rather than a pure 1X2 主胜/平/客胜 field; accuracy is evaluated using the signal's stated coverage."
      },
      "over_under_signal": {
          "n": sum(r["ou_pick"] is not None for r in rows),
          "correct": None,
          "accuracy": None,
          "note": "No per-match O/U line or O/U side is exposed in the current daily HTML cards, so no O/U hit rate is inferred from final scores."
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
