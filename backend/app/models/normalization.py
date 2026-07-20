"""Typed metadata models for canonical audio normalization."""

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

NORMALIZATION_PROFILE_ID = "convointel-stt-wav-v1"
NORMALIZED_AUDIO_RELATIVE_PATH = "normalized/audio.wav"
NORMALIZATION_MEDIA_TYPE = "audio/wav"
NORMALIZATION_CONTAINER = "wav"
NORMALIZATION_CODEC = "pcm_s16le"
NORMALIZATION_SAMPLE_FORMAT = "s16"
NORMALIZATION_SAMPLE_RATE_HZ = 16000
NORMALIZATION_CHANNELS = 1

NormalizationStatus = Literal["normalization_completed"]


class NormalizationProfile(BaseModel):
    """Stable canonical audio profile used by downstream STT phases."""

    model_config = ConfigDict(frozen=True)

    profile_id: Literal["convointel-stt-wav-v1"] = NORMALIZATION_PROFILE_ID
    container: Literal["wav"] = NORMALIZATION_CONTAINER
    codec: Literal["pcm_s16le"] = NORMALIZATION_CODEC
    sample_rate_hz: Literal[16000] = NORMALIZATION_SAMPLE_RATE_HZ
    channels: Literal[1] = NORMALIZATION_CHANNELS
    sample_format: Literal["s16"] = NORMALIZATION_SAMPLE_FORMAT


CANONICAL_NORMALIZATION_PROFILE = NormalizationProfile()


class NormalizationInputMetadata(BaseModel):
    """Source-audio provenance copied from the Phase 2 meeting manifest."""

    relative_path: str = Field(pattern=r"^source/original\.(m4a|mp3|wav)$")
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class NormalizationOutputMetadata(BaseModel):
    """Validated normalized WAV artifact metadata."""

    relative_path: Literal["normalized/audio.wav"] = NORMALIZED_AUDIO_RELATIVE_PATH
    media_type: Literal["audio/wav"] = NORMALIZATION_MEDIA_TYPE
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(ge=0)
    codec: Literal["pcm_s16le"] = NORMALIZATION_CODEC
    sample_rate_hz: Literal[16000] = NORMALIZATION_SAMPLE_RATE_HZ
    channels: Literal[1] = NORMALIZATION_CHANNELS
    sample_format: Literal["s16"] = NORMALIZATION_SAMPLE_FORMAT

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class NormalizationToolMetadata(BaseModel):
    """Concise tool provenance without commands or machine-local paths."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @field_validator("name", "version")
    @classmethod
    def validate_tool_text(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("tool metadata must not be empty")
        return normalized


class NormalizationMetadata(BaseModel):
    """Persisted `metadata/normalization.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    created_at_utc: datetime
    status: NormalizationStatus = "normalization_completed"
    profile: NormalizationProfile
    input: NormalizationInputMetadata
    output: NormalizationOutputMetadata
    tool: NormalizationToolMetadata

    @field_validator("created_at_utc")
    @classmethod
    def validate_created_at_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at_utc must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_serializer("created_at_utc")
    def serialize_created_at_utc(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds",
        ).replace("+00:00", "Z")


class AudioNormalizationResult(BaseModel):
    """Runtime result for local normalization service calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    normalized_audio_path: Path
    normalization_metadata_path: Path
    metadata: NormalizationMetadata
    reused_existing: bool


def _validate_package_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("path must be a safe package-relative path")
