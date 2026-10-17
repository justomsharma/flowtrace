"""Score the held-out test days with the saved model and write dashboard-ready curves.

Writes data/models/triage_curves.json:
  threshold_sweep  precision / recall / alerts at each threshold (drives the UI slider)
  pr_curve         downsampled precision-recall curve for test
  score_hist       score histogram split by label
  confusion        confusion matrix at the chosen threshold
  by_format        recall and precision per payment format

Run:  python -m flowtrace.ml.evaluate
"""
from __future__ import annotations

import json

import numpy as np
import polars as pl
from sklearn.metrics import precision_recall_curve

from flowtrace.ml.model_io import load_model
from flowtrace.ml.train import SPLITS, load_features, to_xy
from flowtrace.settings import settings


def main() -> None:
    s = settings()
    bundle = load_model()
    model, cols, thr = bundle["model"], bundle["features"], bundle["threshold"]

    a, b = SPLITS["test"]
    test = load_features().filter(pl.col("day").is_between(a, b))
    x, y = to_xy(test, cols)
    p = model.predict_proba(x)[:, 1]

    sweep = []
    for t in np.round(np.concatenate([np.linspace(0.01, 0.1, 10), np.linspace(0.15, 0.95, 17)]), 3):
        flag = p >= t
        tp = int((flag & (y == 1)).sum())
        sweep.append({
            "threshold": float(t), "alerts": int(flag.sum()), "tp": tp,
            "precision": tp / max(int(flag.sum()), 1), "recall": tp / int(y.sum()),
        })

    prec, rec, _ = precision_recall_curve(y, p)
    idx = np.linspace(0, len(prec) - 1, 200).astype(int)

    bins = np.linspace(0, 1, 21)
    hist = {
        "bins": bins.round(2).tolist(),
        "legit": np.histogram(p[y == 0], bins)[0].tolist(),
        "laundering": np.histogram(p[y == 1], bins)[0].tolist(),
    }

    flag = p >= thr
    confusion = {
        "tp": int((flag & (y == 1)).sum()), "fp": int((flag & (y == 0)).sum()),
        "fn": int((~flag & (y == 1)).sum()), "tn": int((~flag & (y == 0)).sum()),
    }

    fmt = test["payment_format"].cast(pl.Utf8).to_numpy()
    by_format = []
    for f in sorted(set(fmt)):
        m = fmt == f
        pos = int(y[m].sum())
        tp = int((flag[m] & (y[m] == 1)).sum())
        by_format.append({
            "format": f, "transactions": int(m.sum()), "laundering": pos,
            "flagged": int(flag[m].sum()), "recall": tp / pos if pos else None,
        })

    out = {
        "threshold": thr, "threshold_sweep": sweep,
        "pr_curve": [{"recall": float(rec[i]), "precision": float(prec[i])} for i in idx],
        "score_hist": hist, "confusion": confusion, "by_format": by_format,
    }
    (s.models_dir / "triage_curves.json").write_text(json.dumps(out, indent=2))
    print(f"confusion at {thr:.3f}: {confusion}")


if __name__ == "__main__":
    main()
