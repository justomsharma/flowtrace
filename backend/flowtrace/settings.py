"""Central settings: secrets/connection from .env, tunable thresholds from config.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_ignore_empty=True)

    # 127.0.0.1, not "localhost": on Windows, localhost tries IPv6 first and the first query
    # waits ~21 s for that to fail before falling back (35 ms with 127.0.0.1).
    falkordb_host: str = "127.0.0.1"
    falkordb_port: int = 6379
    falkordb_username: str | None = None   # FalkorDB Cloud: "falkordb"; the local container needs none
    falkordb_password: str | None = None   # from the environment (or .env) only: never in config files or the image
    falkordb_tls: bool = False             # TLS with certificate checks (FalkorDB Cloud's paid tiers; not the free tier)
    graph_name: str = "flowtrace"

    # LLM for the investigation agent and reports. agent_model empty = the provider's default model.
    llm_provider: Literal["openai", "anthropic"] = "openai"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    agent_model: str | None = None

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
