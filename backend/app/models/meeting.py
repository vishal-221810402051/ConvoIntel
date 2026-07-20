"""Meeting package metadata models."""

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

MeetingStatus = Literal["intake_completed"]
AudioExtension = Literal[".m4a", ".mp3", ".wav"]
AudioMediaType = Literal["audio/mp4", "audio/mpeg", "audio/wav"]


class SourceAudioMetadata(BaseModel):
    original_filename: str = Field(min_length=1)
    stored_filename: str = Field(min_length=1)
    relative_path: str = Field(pattern=r"^source/original\.(m4a|mp3|wav)$")
    extension: AudioExtension
    media_type: AudioMediaType
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("original_filename", "stored_filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("filename must not include path separators")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("relative_path must be a safe relative package path")
        return value


class MeetingManifest(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    created_at_utc: datetime
    status: MeetingStatus = "intake_completed"
    source: SourceAudioMetadata

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


class AudioIntakeResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    source_audio_path: Path
    metadata_path: Path
    manifest: MeetingManifest
