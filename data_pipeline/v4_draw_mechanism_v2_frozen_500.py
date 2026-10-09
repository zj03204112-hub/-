import csv, json, math, sqlite3, sys
sys.path.insert(0, "data_pipeline")
import compare_models as cm

DB = "football_model_database.sqlite"
MAP = "model_validation/500_match_to_db_match_id_mapping.csv"
OUT = "model_validation/v4_draw_mechanism_v2_frozen_500.csv"
SUMMARY = "model_validation/v4_draw_mechanism_v2_frozen_500_summary.json"
CUTOFF = "2026-08-22T00:00:00"
HIST = 15
EPS = 1e-9

def actual(r):
    return "H" if r[4] > r[5] else "D" if r[4] == r[5] else "A"

def score_metrics(items):
    n = len(items)
    correct = sum(p == a for p, a in items)
    labels = "HDA"
    macro = 0.0
    per_class = {}
    for c in labels:
        tp = sum(p == c and a == c for p, a in items)
        fp = sum(p == c and a != c for p, a in items)
        fn = sum(p != c and a == c for p, a in items)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        macro += f1 / 3
        per_class[c] = {"predicted": sum(p == c for p, a in items),
                        "actual": sum(a == c for p, a in items),
                        "correct": tp, "precision": precision, "recall": recall, "f1": f1}
    return {"n": n, "correct": correct, "accuracy": correct / n if n else 0,
            "macro_f1": macro, "per_class": per_class}

def sigmoid(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))

def recent_stats(con, team, cutoff, n):
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
    h5 = recent_stats(con, r[2], r[1], 5)
    a5 = recent_stats(con, r[3], r[1], 5)
    h10 = recent_stats(con, r[2], r[1], 10)
    a10 = recent_stats(con, r[3], r[1], 10)
    total = lh + la
    diff = abs(lh - la)
    return [
        math.log(max(total, EPS)), diff, min(lh, la), max(lh, la),
        pd, m[0][0], m[1][1], m[2][2],
        (h5[0] + a5[0]) / 2, abs(h5[0] - a5[0]),
        (h10[0] + a10[0]) / 2, abs(h10[0] - a10[0]),
        (h5[1] + a5[1]) / 2, (h5[2] + a5[2]) / 2,
        abs(h5[3] - a5[3]), (h10[1] + a10[1]) / 2,
        (h10[2] + a10[2]) / 2,
        math.log(max(ph, EPS) / max(pa, EPS))
    ], (ph, pd, pa), (lh, la)

def fit_scaler(xs):
    d = len(xs[0])
    means, stds = [], []
    for j in range(d):
        col = [x[j] for x in xs]
        mu = sum(col) / len(col)
        var = sum((v - mu) ** 2 for v in col) / max(1, len(col))
        means.append(mu)
        stds.append(math.sqrt(var) if var > 1e-10 else 1.0)
    return means, stds

def transform(x, means, stds):
    return [1.0] + [(v - means[j]) / stds[j] for j, v in enumerate(x)]

def fit_logistic(xs, ys, l2=0.08, iterations=2200):
    d = len(xs[0])
    w = [0.0] * d
    n = len(xs)
    for it in range(iterations):
        grad = [0.0] * d
        for x, y in zip(xs, ys):
            e = sigmoid(sum(a * b for a, b in zip(w, x))) - y
            for j in range(d):
                grad[j] += e * x[j]
        lr = 0.08 / (1.0 + it / 900.0)
        for j in range(d):
            g = grad[j] / n + (l2 * w[j] if j else 0.0)
            w[j] -= lr * g
    return w

def draw_probability(w, x):
    return sigmoid(sum(a * b for a, b in zip(w, x)))

def choose_pred(pdraw, ha, threshold):
    ph, pd, pa = ha
    if pdraw >= threshold:
        return "D"
    return "H" if ph >= pa else "A"

def main():
    con = sqlite3.connect(DB)
    cm.HIST_LIMIT = HIST
    with open(MAP, encoding="utf-8-sig") as f:
        mapping = list(csv.DictReader(f))
    assert len(mapping) == 500, f"expected 500 frozen rows, got {len(mapping)}"
    assert len({x["match_id"] for x in mapping}) == 500, "frozen mapping has duplicate match_id"
    db = {r[0]: r for r in con.execute("""
        SELECT m.match_id,m.kickoff,m.home_team,m.away_team,r.ft_home,r.ft_away
        FROM matches m JOIN results r ON r.match_id=m.match_id
        WHERE m.status='finished' AND r.ft_home IS NOT NULL AND r.ft_away IS NOT NULL
    """)}
    assert all(x["match_id"] in db for x in mapping), "frozen mapping IDs do not resolve in Actions DB"
    train_rows = sorted([r for r in db.values() if r[1] < CUTOFF], key=lambda r: r[1])
    test_rows = [db[x["match_id"]] for x in mapping]
    assert len(train_rows) > 2000 and len(test_rows) == 500

    # Chronological validation: fit all learned draw logic on earlier data only;
    # choose the draw threshold on a later pre-cutoff validation segment.
    split = int(len(train_rows) * 0.75)
    early, valid = train_rows[:split], train_rows[split:]
    def estimate_away_bias(rows):
        errs = []
        for r in rows:
            _, la = cm.model_lambdas(con, "v4", r)
            errs.append(la - r[5])
        return sum(errs) / len(errs) if errs else 0.0

    bias_early = estimate_away_bias(early)
    early_data = [features(con, r, bias_early) for r in early]
    valid_data = [features(con, r, bias_early) for r in valid]
    means, stds = fit_scaler([x[0] for x in early_data])
    xearly = [transform(x[0], means, stds) for x in early_data]
    yearly = [1 if actual(r) == "D" else 0 for r in early]
    w_valid = fit_logistic(xearly, yearly)
    valid_x = [transform(x[0], means, stds) for x in valid_data]
    valid_pd = [draw_probability(w_valid, x) for x in valid_x]
    valid_ha = [(d[1][0], d[1][1], d[1][2]) for d in valid_data]
    valid_actual = [actual(r) for r in valid]
    threshold_results = []
    for q in range(10, 61):
        t = q / 100
        preds = [choose_pred(p, ha, t) for p, ha in zip(valid_pd, valid_ha)]
        met = score_metrics(list(zip(preds, valid_actual)))
        threshold_results.append((met["macro_f1"], met["accuracy"], -abs(sum(p == "D" for p in preds) / len(preds) - sum(a == "D" for a in valid_actual) / len(valid_actual)), t, met))
    best = max(threshold_results, key=lambda z: (z[0], z[1], z[2]))
    threshold = best[3]

    # Final frozen-500 run: refit classifier and lambda bias on the entire pre-cutoff pool.
    bias_full = estimate_away_bias(train_rows)
    train_data = [features(con, r, bias_full) for r in train_rows]
    test_data = [features(con, r, bias_full) for r in test_rows]
    means_full, stds_full = fit_scaler([x[0] for x in train_data])
    xtrain = [transform(x[0], means_full, stds_full) for x in train_data]
    ytrain = [1 if actual(r) == "D" else 0 for r in train_rows]
    w_full = fit_logistic(xtrain, ytrain)
    xtest = [transform(x[0], means_full, stds_full) for x in test_data]

    rows = []
    baseline_pairs, new_pairs = [], []
    for z, r, d, x in zip(mapping, test_rows, test_data, xtest):
        pdraw = draw_probability(w_full, x)
        ph, pd, pa = d[1]
        baseline = max((("H", ph), ("D", pd), ("A", pa)), key=lambda t: t[1])[0]
        pred = choose_pred(pdraw, (ph, pd, pa), threshold)
        a = actual(r)
        baseline_pairs.append((baseline, a))
        new_pairs.append((pred, a))
        rows.append({
            "sample_row": z["sample_row"], "match_id": r[0], "kickoff": r[1],
            "home": r[2], "away": r[3], "actual": a,
            "baseline_pred": baseline, "draw_v2_pred": pred,
            "p_draw_classifier": pdraw, "pH_matrix": ph, "pD_matrix": pd, "pA_matrix": pa,
            "lambda_home": d[2][0], "lambda_away_corrected": d[2][1],
            "draw_threshold": threshold
        })

    base_metrics = score_metrics(baseline_pairs)
    new_metrics = score_metrics(new_pairs)
    summary = {
        "experiment": "V4 draw mechanism v2: separate binary draw classifier + conditional H/A decision",
        "production_status": "EXPERIMENT_ONLY",
        "n": 500,
        "cutoff": CUTOFF,
        "hist_window": HIST,
        "train_matches_before_cutoff": len(train_rows),
        "early_train_matches": len(early),
        "chronological_validation_matches": len(valid),
        "features": ["lambda_total", "abs_lambda_gap", "lambda balance", "DC draw/low-score cells",
                     "home/away recent-5 and recent-10 draw rates", "recent goal rates", "H/A matrix log-ratio"],
        "validation": {
            "selected_draw_threshold": threshold,
            "macro_f1": best[0], "accuracy": best[1],
            "predicted_draws": best[4]["per_class"]["D"]["predicted"],
            "actual_draws": best[4]["per_class"]["D"]["actual"],
            "draw_precision": best[4]["per_class"]["D"]["precision"],
            "draw_recall": best[4]["per_class"]["D"]["recall"],
            "threshold_selection": "maximize macro-F1; tie-break overall accuracy, then closest predicted draw rate"
        },
        "estimated_away_lambda_bias_full_train": bias_full,
        "baseline_matrix_argmax": base_metrics,
        "draw_mechanism_v2": new_metrics,
        "improvement_correct": new_metrics["correct"] - base_metrics["correct"],
        "improvement_accuracy": new_metrics["accuracy"] - base_metrics["accuracy"],
        "production_gate": "remain EXPERIMENT_ONLY; require positive out-of-time improvement and nonzero draw recall"
    }
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        wcsv = csv.DictWriter(f, fieldnames=rows[0].keys())
        wcsv.writeheader()
        wcsv.writerows(rows)
    with open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
