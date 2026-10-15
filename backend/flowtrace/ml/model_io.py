"""Save and load the triage model.

Files in data/models/:
  triage_lgbm.pkl      pickled bundle {model, features, threshold} used by the app
  triage_lgbm.txt      LightGBM native booster (portable, loadable without this codebase)
  triage_features.json feature order + decision threshold, for anyone loading the .txt
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


def load_model() -> dict[str, Any]:
    with open(settings().models_dir / PKL, "rb") as f:
        return pickle.load(f)
