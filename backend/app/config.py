"""Typed environment configuration for the Convointel backend."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.core.paths import default_data_dir, resolve_repository_path

APPLICATION_TITLE = "Convointel Backend"
SERVICE_IDENTIFIER = "convointel-backend"
API_VERSION = "v1"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseSettings):
    """Runtime settings loaded from CONVOINTEL_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CONVOINTEL_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
    )

    environment: str = Field(default="development", validation_alias="CONVOINTEL_ENV")
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    log_level: LogLevel = "INFO"
    data_dir: Path = Field(default_factory=default_data_dir)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("CONVOINTEL_ENV must not be empty")
        return normalized

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("CONVOINTEL_HOST must not be empty")
        return normalized

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("CONVOINTEL_LOG_LEVEL must be a string")

        normalized = value.strip().upper()
        if normalized not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(f"CONVOINTEL_LOG_LEVEL must be one of: {allowed}")
        return normalized

    @field_validator("data_dir", mode="before")
    @classmethod
    def normalize_data_dir(cls, value: object) -> Path:
        if value is None or value == "":
            return default_data_dir()
        if isinstance(value, (str, Path)):
            return resolve_repository_path(value)
        raise ValueError("CONVOINTEL_DATA_DIR must be a filesystem path")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the running application."""

    return Settings()
