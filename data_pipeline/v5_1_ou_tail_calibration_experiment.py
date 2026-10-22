#!/usr/bin/env python3
"""Shadow experiment: O/U total-goal calibration plus absolute goal-difference tail calibration.

This script never edits the V5.1 production model or frozen sample. It only writes an
experiment report. Market snapshots must be captured no later than T-12h.
"""
import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "football_model_database.sqlite"
MAP = ROOT / "model_validation/500_match_to_db_match_id_mapping.csv"
OUT = ROOT / "model_validation/v5_1_ou_tail_calibration_result.json"
sys.path.insert(0, str(ROOT / "data_pipeline"))
import compare_models as cm  # noqa: E402

N = 8
RHO = -0.05
REQUIRED = ("ou_line", "over_odds", "under_odds")
MARKET_SCALE_WEIGHT = 0.25
MIN_COMPLETE = 400
MARGIN_BINS = (0, 1, 2, 3)  # final bin means 3+ goals
SMOOTHING = 20.0


def pois(lam, k):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def matrix(lh, la):
    m = []
    for i in range(N):
        row = []
        for j in range(N):
            if i == 0 and j == 0:
                tau = 1 - lh * la * RHO
            elif i == 0 and j == 1:
                tau = 1 + lh * RHO
            elif i == 1 and j == 0:
                tau = 1 + la * RHO
            elif i == 1 and j == 1:
                tau = 1 - RHO
            else:
                tau = 1.0
            row.append(pois(lh, i) * pois(la, j) * tau)
        m.append(row)
    z = sum(map(sum, m))
    return [[v / z for v in row] for row in m]


def total_distribution(m):
    out = defaultdict(float)
    for h in range(N):
        for a in range(N):
            out[h + a] += m[h][a]
    return dict(out)


def outcome_probabilities(m):
    return {
        "H": sum(m[h][a] for h in range(N) for a in range(N) if h > a),
        "D": sum(m[h][a] for h in range(N) for a in range(N) if h == a),
        "A": sum(m[h][a] for h in range(N) for a in range(N) if h < a),
    }


def margin_bin(diff):
    return min(abs(diff), 3)


def implied_over_probability(over_odds, under_odds):
    over_odds, under_odds = float(over_odds), float(under_odds)
    if over_odds <= 1 or under_odds <= 1:
        raise ValueError("decimal odds must be greater than 1")
    a, b = 1 / over_odds, 1 / under_odds
    return a / (a + b)


def over_share_for_line(total_probs, line):
    """Expected share of non-push Asian-total units won by Over; supports quarter lines."""
    line = float(line)
    quarter = abs((line * 4) % 2) > 1e-9  # x.25 or x.75
    component_lines = [line - 0.25, line + 0.25] if quarter else [line]
    wins = losses = 0.0
    for goals, prob in total_probs.items():
        for component in component_lines:
            weight = 1 / len(component_lines)
            if goals > component:
                wins += prob * weight
            elif goals < component:
                losses += prob * weight
    denom = wins + losses
    return wins / denom if denom else 0.5


def market_scale(lh, la, line, over_odds, under_odds):
    target = implied_over_probability(over_odds, under_odds)
    best = (float("inf"), 1.0)
    for step in range(61):
        scale = 0.70 + step * 0.01
        pred = over_share_for_line(total_distribution(matrix(lh * scale, la * scale)), line)
        err = (pred - target) ** 2
        if err < best[0]:
            best = (err, scale)
    return 1 + MARKET_SCALE_WEIGHT * (best[1] - 1)


def apply_ou_market(lh, la, market):
    """Layer 1: fit total-goal shape to the de-vigged O/U signal; layer 2: preserve lambda split."""
    scale = market_scale(lh, la, market["ou_line"], market["over_odds"], market["under_odds"])
    # A common scale preserves the baseline home/away expected-goal ratio.
    return lh * scale, la * scale, scale


def fit_margin_factors(rows):
    """Layer 3: learn absolute goal-difference-bin multipliers on training rows only."""
    observed = [0.0] * 4
    expected = [0.0] * 4
    n = len(rows)
    for row in rows:
        fh, fa = row["actual_home"], row["actual_away"]
        observed[margin_bin(fh - fa)] += 1.0
        for h in range(N):
            for a in range(N):
                expected[margin_bin(h - a)] += row["market_matrix"][h][a]
    factors = []
    for k in range(4):
        prior = expected[k] / max(1, n)
        # Shrink the empirical-to-model ratio toward 1; avoid unstable small-bin multipliers.
        num = observed[k] + SMOOTHING * prior
        den = expected[k] + SMOOTHING * prior
        factors.append(num / den if den else 1.0)
    return factors


def apply_margin_factors(m, factors):
    out = [[m[h][a] * factors[margin_bin(h - a)] for a in range(N)] for h in range(N)]
    z = sum(map(sum, out))
    return [[v / z for v in row] for row in out]


def top_scores(m, n=2):
    return sorted(((m[h][a], h, a) for h in range(N) for a in range(N)), reverse=True)[:n]


def brier(p, actual):
    return sum((p[k] - float(k == actual)) ** 2 for k in ("H", "D", "A"))


def evaluate(rows, matrix_key):
    if not rows:
        return {"n": 0}
    hda_correct = 0
    hda_brier = hda_logloss = score_logloss = total_logloss = 0.0
    total_mae = score1_hits = score2_hits = total_mode_hits = 0
    for row in rows:
        m = row[matrix_key]
        fh, fa = row["actual_home"], row["actual_away"]
        actual = "H" if fh > fa else "D" if fh == fa else "A"
        p = outcome_probabilities(m)
        hda_correct += int(max(p, key=p.get) == actual)
        hda_brier += brier(p, actual)
        hda_logloss -= math.log(max(1e-12, p[actual]))
        if fh < N and fa < N:
            score_logloss -= math.log(max(1e-12, m[fh][fa]))
        else:
            score_logloss -= math.log(1e-12)
        td = total_distribution(m)
        total_logloss -= math.log(max(1e-12, td.get(fh + fa, 0.0)))
        expected_total = sum(t * v for t, v in td.items())
        total_mae += abs(expected_total - fh - fa)
        top = top_scores(m, 2)
        score1_hits += int((top[0][1], top[0][2]) == (fh, fa))
        score2_hits += int((top[1][1], top[1][2]) == (fh, fa))
        total_mode_hits += int(max(td, key=td.get) == fh + fa)
    n = len(rows)
    return {
        "n": n,
        "hda_accuracy": hda_correct / n,
        "hda_brier": hda_brier / n,
        "hda_logloss": hda_logloss / n,
        "score_probability_logloss": score_logloss / n,
        "total_goals_distribution_logloss": total_logloss / n,
        "expected_total_goals_mae": total_mae / n,
        "score1_exact_hit_rate": score1_hits / n,
        "score2_exact_hit_rate": score2_hits / n,
        "most_likely_total_goals_accuracy": total_mode_hits / n,
    }


def main():
    if not DB.exists() or not MAP.exists():
        print(json.dumps({"status": "BLOCKED_MISSING_DB_OR_MAPPING", "db_exists": DB.exists(), "mapping_exists": MAP.exists()}))
        raise SystemExit(0)

    con = sqlite3.connect(DB)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "market_snapshot" not in tables:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"status": "BLOCKED_MARKET_TABLE_MISSING", "required_fields": list(REQUIRED)}, indent=2), encoding="utf-8")
        print(json.dumps({"status": "BLOCKED_MARKET_TABLE_MISSING"}))
        return
    cols = {r[1] for r in con.execute("PRAGMA table_info(market_snapshot)")}
    required_cols = {"match_id", "captured_at", "ou_line", "over_odds", "under_odds"}
    if not required_cols.issubset(cols):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"status": "BLOCKED_MARKET_SCHEMA", "available_columns": sorted(cols)}, indent=2), encoding="utf-8")
        print(json.dumps({"status": "BLOCKED_MARKET_SCHEMA", "available_columns": sorted(cols)}))
        return

    with MAP.open(encoding="utf-8-sig", newline="") as f:
        mapping = list(csv.DictReader(f))
    if len(mapping) != 500 or len({x.get("match_id", "") for x in mapping}) != 500 or any(x.get("mapping_status") != "unique" for x in mapping):
        raise RuntimeError("Frozen 500 mapping integrity check failed; no experiment run.")

    db_rows = {}
    for r in con.execute("""SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL"""):
        db_rows[r[0]] = r

    rows = []
    missing = 0
    for entry in mapping:
        r = db_rows.get(entry["match_id"])
        if not r:
            missing += 1
            continue
        mid, kickoff, home, away, fh, fa = r
        cutoff = (datetime.fromisoformat(kickoff[:19]) - timedelta(hours=12)).isoformat(timespec="seconds")
        market_row = con.execute("""SELECT ou_line,over_odds,under_odds FROM market_snapshot
            WHERE match_id=? AND captured_at<=? AND ou_line IS NOT NULL
              AND over_odds IS NOT NULL AND under_odds IS NOT NULL
            ORDER BY captured_at DESC LIMIT 1""", (mid, cutoff)).fetchone()
        if not market_row:
            missing += 1
            continue
        market = dict(zip(REQUIRED, market_row))
        try:
            lh, la = cm.model_lambdas(con, "v4", r)
            base = cm.matrix(lh, la, True)
            nh, na, scale = apply_ou_market(lh, la, market)
            market_matrix = matrix(nh, na)
        except Exception as exc:
            missing += 1
            continue
        rows.append({
            "match_id": mid, "kickoff": kickoff, "home": home, "away": away,
            "actual_home": int(fh), "actual_away": int(fa),
            "market": market, "lambda_base": [lh, la], "lambda_market": [nh, na],
            "ou_scale": scale, "base_matrix": base, "market_matrix": market_matrix,
        })

    rows.sort(key=lambda x: (x["kickoff"], x["match_id"]))
    coverage = {"frozen_sample_n": 500, "complete_t12h_ou_market_n": len(rows), "missing_or_unresolved_n": 500 - len(rows)}
    if len(rows) < MIN_COMPLETE:
        payload = {
            "status": "BLOCKED_INSUFFICIENT_T12H_OU_COVERAGE",
            "coverage": coverage,
            "reason": "Need at least 400/500 unique matches with a complete, timestamped O/U snapshot captured by T-12h. No missing market data is inferred.",
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return

    split = int(len(rows) * 0.60)
    train, test = rows[:split], rows[split:]
    factors = fit_margin_factors(train)
    for row in rows:
        row["tail_matrix"] = apply_margin_factors(row["market_matrix"], factors)

    baseline = evaluate(test, "base_matrix")
    market = evaluate(test, "market_matrix")
    tail = evaluate(test, "tail_matrix")
    # Conservative acceptance gate: no H/D/A probability degradation, better total-goal
    # distribution loss, and no worsening in exact-score probability loss.
    hda_ok = tail["hda_brier"] <= baseline["hda_brier"] + 1e-9 and tail["hda_logloss"] <= baseline["hda_logloss"] + 1e-9
    score_ok = tail["score_probability_logloss"] <= baseline["score_probability_logloss"] + 1e-9
    total_ok = tail["total_goals_distribution_logloss"] < baseline["total_goals_distribution_logloss"]
    accepted = hda_ok and score_ok and total_ok
    payload = {
        "status": "EXPERIMENT_EVALUATED",
        "production_acceptance": "ACCEPT_SHADOW_CALIBRATOR" if accepted else "REJECT_OR_REVISE",
        "coverage": coverage,
        "baseline_source": "compare_models.model_lambdas(model='v4') + Dixon-Coles matrix; verify exact equivalence to the currently deployed V5.1 lambda source before production claims",
        "market_policy": "Only O/U snapshots captured at or before kickoff minus 12 hours; no post-kickoff or closing-line substitution",
        "method": {
            "layer1": "de-vig Over/Under odds and Asian-total line select a common lambda scale",
            "layer2": "common lambda scale preserves the baseline home/away lambda ratio; no forced home-team uplift",
            "layer3": "absolute goal-difference bins 0, 1, 2, 3+ receive train-only shrunken multipliers",
            "layer4": "chronological 60/40 split; test metrics reported separately; V5.1 production files untouched",
            "market_scale_weight": MARKET_SCALE_WEIGHT,
            "margin_bins": ["0", "1", "2", "3+"],
            "margin_factors_fit_on_train": factors,
        },
        "split": {"train_n": len(train), "test_n": len(test)},
        "test_metrics": {"baseline": baseline, "ou_market": market, "ou_market_plus_tail": tail},
        "acceptance_checks": {
            "hda_brier_and_logloss_not_worse": hda_ok,
            "score_probability_logloss_not_worse": score_ok,
            "total_goals_distribution_logloss_improved": total_ok,
            "accepted": accepted,
        },
        "sample_rows": [{
            "match_id": r["match_id"], "kickoff": r["kickoff"], "home": r["home"], "away": r["away"],
            "actual_score": f'{r["actual_home"]}-{r["actual_away"]}',
            "ou_line": r["market"]["ou_line"], "over_odds": r["market"]["over_odds"],
            "under_odds": r["market"]["under_odds"], "ou_scale": r["ou_scale"],
        } for r in rows],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "sample_rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
