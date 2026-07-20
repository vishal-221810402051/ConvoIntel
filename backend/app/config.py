"""Typed environment configuration for the Convointel backend."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.core.paths import default_data_dir, resolve_repository_path

APPLICATION_TITLE = "Convointel Backend"
SERVICE_IDENTIFIER = "convointel-backend"
API_VERSION = "v1"
TRANSCRIPTION_MODEL = "gpt-4o-transcribe-diarize"
CLEANUP_MODEL = "gpt-5-mini-2025-08-07"
INTELLIGENCE_MODEL = "gpt-5-mini-2025-08-07"
TEMPORAL_MODEL = "gpt-5-mini-2025-08-07"

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
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    normalization_timeout_seconds: int = Field(default=1800, ge=1, le=86400)
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CONVOINTEL_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    transcription_model: str = TRANSCRIPTION_MODEL
    transcription_timeout_seconds: int = Field(default=1800, ge=1, le=86400)
    transcription_max_retries: int = Field(default=2, ge=0, le=10)
    transcription_language: str | None = None
    cleanup_model: str = CLEANUP_MODEL
    cleanup_timeout_seconds: int = Field(default=900, ge=1, le=86400)
    cleanup_max_retries: int = Field(default=2, ge=0, le=10)
    cleanup_max_batch_characters: int = Field(default=50000, ge=1, le=1000000)
    cleanup_max_output_tokens: int = Field(default=16000, ge=1, le=100000)
    intelligence_model: str = INTELLIGENCE_MODEL
    intelligence_timeout_seconds: int = Field(default=1200, ge=1, le=86400)
    intelligence_max_retries: int = Field(default=2, ge=0, le=10)
    intelligence_max_input_characters: int = Field(
        default=500000,
        ge=1,
        le=5000000,
    )
    intelligence_max_output_tokens: int = Field(default=32000, ge=1, le=100000)
    intelligence_max_items_per_category: int = Field(default=100, ge=1, le=500)
    temporal_model: str = TEMPORAL_MODEL
    temporal_timeout_seconds: int = Field(default=1200, ge=1, le=86400)
    temporal_max_retries: int = Field(default=2, ge=0, le=10)
    temporal_max_input_characters: int = Field(default=600000, ge=1, le=5000000)
    temporal_max_output_tokens: int = Field(default=24000, ge=1, le=100000)
    temporal_max_items: int = Field(default=300, ge=1, le=1000)

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

    @field_validator("ffmpeg_binary", "ffprobe_binary")
    @classmethod
    def validate_executable_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("FFmpeg and FFprobe binary settings must not be empty")
        return normalized

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def normalize_openai_api_key(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            secret_value = value.get_secret_value().strip()
            return SecretStr(secret_value) if secret_value else None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        raise ValueError("OpenAI API key must be a string")

    @field_validator("transcription_model")
    @classmethod
    def validate_transcription_model(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != TRANSCRIPTION_MODEL:
            raise ValueError(
                f"CONVOINTEL_TRANSCRIPTION_MODEL must be {TRANSCRIPTION_MODEL}"
            )
        return normalized

    @field_validator("cleanup_model")
    @classmethod
    def validate_cleanup_model(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != CLEANUP_MODEL:
            raise ValueError(f"CONVOINTEL_CLEANUP_MODEL must be {CLEANUP_MODEL}")
        return normalized

    @field_validator("intelligence_model")
    @classmethod
    def validate_intelligence_model(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != INTELLIGENCE_MODEL:
            raise ValueError(
                f"CONVOINTEL_INTELLIGENCE_MODEL must be {INTELLIGENCE_MODEL}"
            )
        return normalized

    @field_validator("temporal_model")
    @classmethod
    def validate_temporal_model(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != TEMPORAL_MODEL:
            raise ValueError(f"CONVOINTEL_TEMPORAL_MODEL must be {TEMPORAL_MODEL}")
        return normalized

    @field_validator("transcription_language", mode="before")
    @classmethod
    def validate_transcription_language(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("CONVOINTEL_TRANSCRIPTION_LANGUAGE must be a string")
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) != 2 or not normalized.isascii() or not normalized.islower():
            raise ValueError(
                "CONVOINTEL_TRANSCRIPTION_LANGUAGE must be a lowercase ISO-639-1 code"
            )
        if not normalized.isalpha():
            raise ValueError(
                "CONVOINTEL_TRANSCRIPTION_LANGUAGE must be a lowercase ISO-639-1 code"
            )
        return normalized

    @property
    def meetings_dir(self) -> Path:
        return (self.data_dir / "meetings").resolve(strict=False)


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the running application."""

    return Settings()
