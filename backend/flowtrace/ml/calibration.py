"""Map the triage model's raw score to a probability of laundering (isotonic, fitted on validation days).

The curve is stored as breakpoints in config.yaml (`calibration.x`, `calibration.y`) by
`python -m flowtrace.eval.tune`, so the app needs nothing beyond the config to apply it.
"""
from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression

# Dense where the model separates classes (near 0 and near 1), sparse in between.
GRID = np.r_[np.linspace(0, 0.1, 11), np.linspace(0.15, 0.9, 16), np.linspace(0.92, 1.0, 5)]


def fit(p: np.ndarray, y: np.ndarray) -> dict:
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y)
    ys = np.maximum.accumulate(iso.predict(GRID))
    return {"x": [round(float(v), 4) for v in GRID], "y": [round(float(v), 5) for v in ys]}


def calibrate(p: np.ndarray, rules: dict) -> np.ndarray:
    c = rules["calibration"]
    return np.clip(np.interp(np.asarray(p, dtype=float), c["x"], c["y"]), 0.0, 1.0)
