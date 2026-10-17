"""Save and load the triage model.

Files in data/models/:
  triage_lgbm.pkl      pickled bundle {model, features, threshold} used by the app
  triage_lgbm.txt      LightGBM native booster (portable, loadable without this codebase)
  triage_features.json feature order + decision threshold, for anyone loading the .txt

Without the .pkl (a fresh clone: *.pkl is not in git), load_model() uses the .txt booster and features file; the
demo bundle (data/demo/model) carries the ones the deployed app uses. Same trees, same scores.
"""
from __future__ import annotations

import json
import pickle
from typing import Any

from flowtrace.settings import settings

PKL = "triage_lgbm.pkl"
TXT = "triage_lgbm.txt"
FEATURES = "triage_features.json"


def save_model(model: Any, features: list[str], threshold: float) -> None:
    d = settings().models_dir
    d.mkdir(parents=True, exist_ok=True)
    with open(d / PKL, "wb") as f:
        pickle.dump({"model": model, "features": features, "threshold": threshold}, f)
    model.booster_.save_model(str(d / TXT))
    (d / FEATURES).write_text(json.dumps({"features": features, "threshold": threshold}, indent=2))


class _Booster:
    """The .txt booster behind the predict_proba the app calls on the pickled classifier."""

    def __init__(self, path) -> None:
        import lightgbm as lgb
        self.booster = lgb.Booster(model_file=str(path))

    def predict_proba(self, x):
        import numpy as np
        p = self.booster.predict(x)
        return np.column_stack([1 - p, p])


def load_model() -> dict[str, Any]:
    d = settings().models_dir
    if (d / PKL).exists():
        with open(d / PKL, "rb") as f:
            return pickle.load(f)
    meta = json.loads((d / FEATURES).read_text())
    return {"model": _Booster(d / TXT), "features": meta["features"], "threshold": meta["threshold"]}
