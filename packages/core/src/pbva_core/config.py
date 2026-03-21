"""Application configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    data_root: Path = Path("data")
    db_path: Path = Path("data/pbuddy.db")
    worker_poll_interval_s: float = 2.0
    max_concurrent_jobs: int = 1
    ffmpeg_bin: str = "ffmpeg"
    hardware_backend: Literal["cpu", "metal", "cuda"] = "cpu"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PBUDDY_")


# Module-level singleton; callers can override by constructing Settings() directly.
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
