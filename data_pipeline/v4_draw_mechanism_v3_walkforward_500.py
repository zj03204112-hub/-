import csv, json, math, sqlite3, sys
from datetime import datetime
sys.path.insert(0, "data_pipeline")
import compare_models as cm

DB = "football_model_database.sqlite"
MAP = "model_validation/500_match_to_db_match_id_mapping.csv"
OUT = "model_validation/v4_draw_v3_walkforward_500.csv"
SUMMARY = "model_validation/v4_draw_v3_walkforward_500_summary.json"
HIST = 15
EPS = 1e-9

def actual(r):
    return "H" if r[4] > r[5] else "D" if r[4] == r[5] else "A"

def dt(s):
    return datetime.fromisoformat(str(s)[:19])

def score_metrics(items):
    n = len(items)
    per = {}
    for c in "HDA":
        tp = sum(p == c and a == c for p, a in items)
        fp = sum(p == c and a != c for p, a in items)
        fn = sum(p != c and a == c for p, a in items)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per[c] = {"predicted": sum(p == c for p, a in items), "actual": sum(a == c for p, a in items),
                  "correct": tp, "precision": precision, "recall": recall, "f1": f1}
    return {"n": n, "correct": sum(p == a for p, a in items), "accuracy": sum(p == a for p, a in items) / n,
            "macro_f1": sum(v["f1"] for v in per.values()) / 3, "per_class": per}

def sigmoid(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))

def recent_stats(con, team, kickoff, n):
    from datetime import timedelta
    cutoff = (dt(kickoff) - timedelta(hours=12)).isoformat(timespec="seconds")
    h = cm.hist(con, team, cutoff)[:n]
    if not h:
        return [0.25, 1.15, 1.15, 0.0]
    draws = sum(gf == ga for (gf, ga), opp in h) / len(h)
    gf = sum(x[0][0] for x in h) / len(h)
    ga = sum(x[0][1] for x in h) / len(h)
    gd = sum(x[0][0] - x[0][1] for x in h) / len(h)
    return [draws, gf, ga, gd]

def features(con, r, away_bias):
    lh, la = cm.model_lambdas(con, "v4", r)
    la = max(0.05, la - away_bias)
    m = cm.matrix(lh, la, True)
    ph = sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i > j)
    pd = sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i == j)
    pa = sum(m[i][j] for i in range(cm.N) for j in range(cm.N) if i < j)
    h5, a5 = recent_stats(con, r[2], r[1], 5), recent_stats(con, r[3], r[1], 5)
    h10, a10 = recent_stats(con, r[2], r[1], 10), recent_stats(con, r[3], r[1], 10)
    total, diff = lh + la, abs(lh - la)
    x = [
        math.log(max(total, EPS)), diff, min(lh, la), max(lh, la), pd,
        m[0][0], m[1][1], m[2][2],
        (h5[0] + a5[0]) / 2, abs(h5[0] - a5[0]),
        (h10[0] + a10[0]) / 2, abs(h10[0] - a10[0]),
        (h5[1] + a5[1]) / 2, (h5[2] + a5[2]) / 2,
        abs(h5[3] - a5[3]), (h10[1] + a10[1]) / 2,
        (h10[2] + a10[2]) / 2, math.log(max(ph, EPS) / max(pa, EPS))
    ]
    return x, (ph, pd, pa), (lh, la)

def fit_scaler(xs):
    means, stds = [], []
    for col in zip(*xs):
        mu = sum(col) / len(col)
        var = sum((v - mu) ** 2 for v in col) / max(1, len(col))
        means.append(mu)
        stds.append(math.sqrt(var) if var > 1e-10 else 1.0)
    return means, stds

def transform(x, means, stds):
    return [1.0] + [(v - means[j]) / stds[j] for j, v in enumerate(x)]

def fit_logistic(xs, ys, l2=0.08, iterations=2200):
    w, n, d = [0.0] * len(xs[0]), len(xs), len(xs[0])
    for it in range(iterations):
        grad = [0.0] * d
        for x, y in zip(xs, ys):
            e = sigmoid(sum(a * b for a, b in zip(w, x))) - y
            for j in range(d):
                grad[j] += e * x[j]
        lr = 0.08 / (1.0 + it / 900.0)
        for j in range(d):
            w[j] -= lr * (grad[j] / n + (l2 * w[j] if j else 0.0))
    return w

def probs(w, x):
    return sigmoid(sum(a * b for a, b in zip(w, x)))

def predict(pdraw, ha, threshold):
    ph, pd, pa = ha
    if pdraw >= threshold:
        return "D"
    return "H" if ph >= pa else "A"

def brier_logloss(prob_rows, labels):
    brier, loss = 0.0, 0.0
    index = {"H": 0, "D": 1, "A": 2}
    for p, y in zip(prob_rows, labels):
        brier += sum((p[j] - (1.0 if j == index[y] else 0.0)) ** 2 for j in range(3))
        loss -= math.log(max(EPS, p[index[y]))
    n = len(labels)
    return {"brier": brier / n, "log_loss": loss / n}

def main():
    con = sqlite3.connect(DB)
    cm.HIST_LIMIT = HIST
    with open(MAP, encoding="utf-8-sig") as f:
        mapping = list(csv.DictReader(f))
    assert len(mapping) == 500 and len({x["match_id"] for x in mapping}) == 500, "Need exactly 500 unique frozen matches"
    db = {r[0]: r for r in con.execute("""
        SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
    """)}
    assert all(x["match_id"] in db for x in mapping), "Unresolved frozen match IDs"
    rows = sorted([db[x["match_id"]] for x in mapping], key=lambda r: (dt(r[1]), r[0]))
    assert len(rows) == 500
    # Strict chronological 300 train / 100 threshold-calibration / 100 blind holdout.
    train, cal, hold = rows[:300], rows[300:400], rows[400:]
    assert dt(train[-1][1]) <= dt(cal[0][1]) <= dt(cal[-1][1]) <= dt(hold[0][1]), "Chronology split invalid"

    def estimate_away_bias(rs):
        err = []
        for r in rs:
            _, la = cm.model_lambdas(con, "v4", r)
            err.append(la - r[5])
        return sum(err) / len(err) if err else 0.0

    bias_train = estimate_away_bias(train)
    train_data = [features(con, r, bias_train) for r in train]
    cal_data = [features(con, r, bias_train) for r in cal]
    means, stds = fit_scaler([z[0] for z in train_data])
    xtrain = [transform(z[0], means, stds) for z in train_data]
    ytrain = [1 if actual(r) == "D" else 0 for r in train]
    w = fit_logistic(xtrain, ytrain)
    xcal = [transform(z[0], means, stds) for z in cal_data]
    pcal = [probs(w, x) for x in xcal]
    ha_cal = [z[1] for z in cal_data]
    ycal = [actual(r) for r in cal]
    candidates = []
    for q in range(10, 61):
        threshold = q / 100
        pred = [predict(p, ha, threshold) for p, ha in zip(pcal, ha_cal)]
        met = score_metrics(list(zip(pred, ycal)))
        draw = met["per_class"]["D"]
        candidates.append((met["macro_f1"], met["accuracy"], draw["recall"], -abs(draw["predicted"] / len(cal) - draw["actual"] / len(cal)), threshold, met))
    best = max(candidates, key=lambda z: (z[0], z[1], z[2], z[3]))
    threshold = best[4]

    # Refit on the first 400 chronological rows; do not touch the final 100 labels for selection.
    bias_full = estimate_away_bias(rows[:400])
    fit_data = [features(con, r, bias_full) for r in rows[:400]]
    hold_data = [features(con, r, bias_full) for r in hold]
    means2, stds2 = fit_scaler([z[0] for z in fit_data])
    xfit = [transform(z[0], means2, stds2) for z in fit_data]
    w2 = fit_logistic(xfit, [1 if actual(r) == "D" else 0 for r in rows[:400]])
    xhold = [transform(z[0], means2, stds2) for z in hold_data]
    phold_draw = [probs(w2, x) for x in xhold]
    actual_hold = [actual(r) for r in hold]
    baseline_probs = [z[1] for z in hold_data]
    v3_probs, baseline_pred, v3_pred, out_rows = [], [], [], []
    for r, z, pd, x in zip(hold, hold_data, phold_draw, xhold):
        ph, pmat_d, pa = z[1]
        v3p = [((1 - pd) * ph / max(EPS, ph + pa)), pd, ((1 - pd) * pa / max(EPS, ph + pa))]
        v3_probs.append(v3p)
        baseline_pred.append(max(zip("HDA", [ph, pmat_d, pa]), key=lambda z: z[1])[0])
        v3_pred.append(predict(pd, (ph, pmat_d, pa), threshold))
        out_rows.append({
            "match_id": r[0], "kickoff": r[1], "home": r[2], "away": r[3], "actual": actual(r),
            "baseline_pred": baseline_pred[-1], "v3_pred": v3_pred[-1],
            "p_draw_v3": pd, "p_home_matrix": ph, "p_draw_matrix": pmat_d, "p_away_matrix": pa,
            "lambda_home": z[2][0], "lambda_away_corrected": z[2][1], "draw_threshold": threshold
        })
    base = score_metrics(list(zip(baseline_pred, actual_hold)))
    new = score_metrics(list(zip(v3_pred, actual_hold)))
    summary = {
        "experiment": "V4 draw mechanism V3 strict chronological 300/100/100",
        "status": "EXPERIMENT_ONLY",
        "n": 500, "split": {"train": 300, "threshold_calibration": 100, "blind_holdout": 100,
                             "train_end": train[-1][1], "calibration_start": cal[0][1],
                             "calibration_end": cal[-1][1], "holdout_start": hold[0][1]},
        "features": ["lambda total", "absolute lambda gap", "minimum/maximum lambda", "Poisson-Dixon-Coles draw probability",
                     "0-0/1-1/2-2 score cells", "recent-5/10 draw rates", "recent goal rates", "H/A probability log ratio"],
        "selected_draw_threshold_on_calibration_only": threshold,
        "calibration_metrics": best[5],
        "baseline_holdout": {**base, **brier_logloss(baseline_probs, actual_hold)},
        "v3_holdout": {**new, **brier_logloss(v3_probs, actual_hold)},
        "delta_accuracy_pp": round((new["accuracy"] - base["accuracy"]) * 100, 4),
        "delta_draw_recall_pp": round((new["per_class"]["D"]["recall"] - base["per_class"]["D"]["recall"]) * 100, 4),
        "production_gate": "EXPERIMENT_ONLY; promote only if holdout draw recall improves and overall accuracy/Brier/LogLoss do not materially worsen"
    }
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        wcsv = csv.DictWriter(f, fieldnames=out_rows[0].keys())
        wcsv.writeheader()
        wcsv.writerows(out_rows)
    with open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
