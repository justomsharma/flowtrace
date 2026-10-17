"""Train the LightGBM triage model with a chronological split and write honest metrics.

Split (end-of-day batches, no shuffling):
  train  2022-09-01 .. 09-06
  val    2022-09-07 .. 09-08   early stopping + threshold selection
  test   2022-09-09 .. 09-10   reported once, never tuned on
  Sep 11-18 is excluded: legitimate activity stops on Sep 10 while laundering rows continue
  (1,108 rows, ~59% illicit), so including it would inflate every metric.

Run:  python -m flowtrace.ml.train
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.metrics import (
    accuracy_score, average_precision_score, f1_score, precision_recall_curve,
    precision_score, recall_score, roc_auc_score,
)

from flowtrace.ml.features import CATEGORICAL, build_features, feature_columns
from flowtrace.ml.model_io import save_model
from flowtrace.settings import settings

EPOCH = date(1970, 1, 1)


def day_of(d: date) -> int:
    """Timestamps are naive and stored as UTC epoch ms, so day index = days since 1970-01-01."""
    return (d - EPOCH).days


SPLITS = {
    "train": (day_of(date(2022, 9, 1)), day_of(date(2022, 9, 6))),
    "val": (day_of(date(2022, 9, 7)), day_of(date(2022, 9, 8))),
    "test": (day_of(date(2022, 9, 9)), day_of(date(2022, 9, 10))),
}


def to_xy(df: pl.DataFrame, cols: list[str]):
    pdf = df.select(cols).to_pandas()
    for c in CATEGORICAL:
        pdf[c] = pdf[c].astype("category")
    return pdf, df["is_laundering"].to_numpy()


def metrics(y, p, thr) -> dict:
    yhat = (p >= thr).astype(int)
    order = np.argsort(-p)
    out = {
        "n": int(len(y)), "positives": int(y.sum()), "threshold": float(thr),
        "pr_auc": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "f1": float(f1_score(y, yhat)),
        "precision": float(precision_score(y, yhat, zero_division=0)),
        "recall": float(recall_score(y, yhat)),
        "accuracy": float(accuracy_score(y, yhat)),
        "accuracy_all_legit_baseline": float(1 - y.mean()),
        "flagged": int(yhat.sum()),
    }
    for k in (100, 500, 1000):
        out[f"precision_at_{k}"] = float(y[order[:k]].mean())
    return out


NEG_SAMPLE = 0.2  # share of legitimate training rows kept; val/test are never sampled


def load_features(rebuild: bool = False) -> pl.DataFrame:
    s = settings()
    path = s.processed_dir / "features.parquet"
    if rebuild or not path.exists():
        t0 = time.time()
        tx = pl.read_parquet(s.processed_dir / "transactions.parquet")
        acc = pl.read_parquet(s.processed_dir / "accounts.parquet")
        build_features(tx, acc).write_parquet(path)
        print(f"features built in {time.time() - t0:.0f}s -> {path.name}")
    return pl.read_parquet(path)


def main() -> None:
    s = settings()
    t0 = time.time()
    feats = load_features()
    cols = feature_columns(feats)

    parts = {k: feats.filter(pl.col("day").is_between(a, b)) for k, (a, b) in SPLITS.items()}
    del feats
    for k, v in parts.items():
        print(f"  {k}: {v.height:,} rows, {v['is_laundering'].sum():,} laundering")
    tr = parts["train"]
    parts["train"] = pl.concat([
        tr.filter(pl.col("is_laundering") == 1),
        tr.filter(pl.col("is_laundering") == 0).sample(fraction=NEG_SAMPLE, seed=42),
    ])
    print(f"  train after negative sampling: {parts['train'].height:,}")

    xtr, ytr = to_xy(parts["train"], cols)
    xva, yva = to_xy(parts["val"], cols)
    xte, yte = to_xy(parts["test"], cols)

    model = lgb.LGBMClassifier(
        n_estimators=2000, learning_rate=0.03, num_leaves=63, min_child_samples=50,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
        scale_pos_weight=1.0, random_state=42, verbose=-1,
    )
    model.fit(
        xtr, ytr, eval_set=[(xva, yva)], eval_metric="average_precision",
        callbacks=[lgb.early_stopping(100, verbose=False), lgb.log_evaluation(200)],
    )

    pva = model.predict_proba(xva)[:, 1]
    prec, rec, thr = precision_recall_curve(yva, pva)
    f1s = 2 * prec[:-1] * rec[:-1] / np.clip(prec[:-1] + rec[:-1], 1e-9, None)
    best_thr = float(thr[np.argmax(f1s)])

    pte = model.predict_proba(xte)[:, 1]
    report = {
        "split": {k: [str(EPOCH + timedelta(days=a)), str(EPOCH + timedelta(days=b))] for k, (a, b) in SPLITS.items()},
        "best_iteration": int(model.best_iteration_),
        "val": metrics(yva, pva, best_thr),
        "test": metrics(yte, pte, best_thr),
        "feature_importance_gain": dict(sorted(
            zip(cols, model.booster_.feature_importance("gain").round(1).tolist()),
            key=lambda kv: -kv[1],
        )),
        "features": cols,
        "train_negative_sample_rate": NEG_SAMPLE,
        "train_seconds": round(time.time() - t0, 1),
    }
    s.models_dir.mkdir(parents=True, exist_ok=True)
    save_model(model, cols, best_thr)
    (s.models_dir / "triage_metrics.json").write_text(json.dumps(report, indent=2))

    for k in ("val", "test"):
        m = report[k]
        print(f"{k}: PR-AUC {m['pr_auc']:.3f}  F1 {m['f1']:.3f}  P {m['precision']:.3f}  R {m['recall']:.3f}  "
              f"acc {m['accuracy']:.5f} (all-legit {m['accuracy_all_legit_baseline']:.5f})  P@100 {m['precision_at_100']:.2f}")
    print("top features:", list(report["feature_importance_gain"])[:12])


if __name__ == "__main__":
    main()
