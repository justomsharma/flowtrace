"""Central settings: secrets/connection from .env, tunable thresholds from config.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_ignore_empty=True)

    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_password: str | None = None
    graph_name: str = "flowtrace"

    anthropic_api_key: str | None = None
    agent_model: str = "claude-sonnet-5-5"

    dataset: str = "HI-Small"
    raw_dir: Path = ROOT / "data" / "raw"
    processed_dir: Path = ROOT / "data" / "processed"
    models_dir: Path = ROOT / "data" / "models"
    config_file: Path = ROOT / "config.yaml"


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def cfg() -> dict[str, Any]:
    """Thresholds and weights. Every number used for flagging lives in config.yaml."""
    with open(settings().config_file, encoding="utf-8") as f:
        return yaml.safe_load(f)
