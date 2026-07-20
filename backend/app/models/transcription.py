"""Typed models for diarized raw transcription artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.config import TRANSCRIPTION_MODEL
from backend.app.models.normalization import (
    NORMALIZATION_PROFILE_ID,
    NORMALIZED_AUDIO_RELATIVE_PATH,
)

TRANSCRIPTION_PROVIDER_NAME = "openai"
TRANSCRIPTION_RESPONSE_FORMAT = "diarized_json"
TRANSCRIPTION_CHUNKING_STRATEGY = "auto"
TRANSCRIPT_JSON_RELATIVE_PATH = "transcript/raw.json"
TRANSCRIPT_TEXT_RELATIVE_PATH = "transcript/raw.txt"
TRANSCRIPTION_METADATA_RELATIVE_PATH = "metadata/transcription.json"
SEGMENT_OVERLAP_TOLERANCE_SECONDS = 0.001

TranscriptionStatus = Literal["transcription_completed"]


class TranscriptSegment(BaseModel):
    """Timestamped anonymous speaker segment."""

    segment_id: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    speaker_label: str = Field(min_length=1)
    text: str

    @field_validator("segment_id", "speaker_label")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("segment identifiers and speaker labels must be nonempty")
        return normalized

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "TranscriptSegment":
        if self.end_seconds < self.start_seconds:
            raise ValueError("segment end must not be before start")
        return self


class RawTranscript(BaseModel):
    """Provider-independent canonical raw transcript."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    text: str
    duration_seconds: float = Field(ge=0)
    segments: list[TranscriptSegment]

    @model_validator(mode="after")
    def validate_segments(self) -> "RawTranscript":
        seen_ids: set[str] = set()
        previous_start: float | None = None
        previous_end: float | None = None

        for segment in self.segments:
            if segment.segment_id in seen_ids:
                raise ValueError("segment IDs must be unique")
            seen_ids.add(segment.segment_id)

            if previous_start is not None and (
                segment.start_seconds + SEGMENT_OVERLAP_TOLERANCE_SECONDS
                < previous_start
            ):
                raise ValueError("segments must be ordered by start time")
            if previous_end is not None and (
                segment.start_seconds + SEGMENT_OVERLAP_TOLERANCE_SECONDS
                < previous_end
            ):
                raise ValueError("segments must not overlap")

            previous_start = segment.start_seconds
            previous_end = segment.end_seconds

        return self

    @property
    def speaker_labels(self) -> list[str]:
        return sorted({segment.speaker_label for segment in self.segments})


class TranscriptionProviderMetadata(BaseModel):
    """Persisted provider request contract."""

    name: Literal["openai"] = TRANSCRIPTION_PROVIDER_NAME
    model: Literal["gpt-4o-transcribe-diarize"] = TRANSCRIPTION_MODEL
    response_format: Literal["diarized_json"] = TRANSCRIPTION_RESPONSE_FORMAT
    chunking_strategy: Literal["auto"] = TRANSCRIPTION_CHUNKING_STRATEGY
    requested_language: str | None = None

    @field_validator("requested_language")
    @classmethod
    def validate_requested_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 2 or not value.isascii() or not value.islower() or not value.isalpha():
            raise ValueError("requested_language must be a lowercase ISO-639-1 code")
        return value


class TranscriptionInputMetadata(BaseModel):
    """Normalized-audio provenance used for transcription."""

    relative_path: Literal["normalized/audio.wav"] = NORMALIZED_AUDIO_RELATIVE_PATH
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(ge=0)
    normalization_profile: Literal["convointel-stt-wav-v1"] = NORMALIZATION_PROFILE_ID


class TranscriptionArtifactsMetadata(BaseModel):
    """Published raw transcript artifact metadata."""

    text_relative_path: Literal["transcript/raw.txt"] = TRANSCRIPT_TEXT_RELATIVE_PATH
    text_size_bytes: int = Field(gt=0)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structured_relative_path: Literal["transcript/raw.json"] = TRANSCRIPT_JSON_RELATIVE_PATH
    structured_size_bytes: int = Field(gt=0)
    structured_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_count: int = Field(ge=0)
    speaker_labels: list[str]

    @field_validator("text_relative_path", "structured_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value

    @field_validator("speaker_labels")
    @classmethod
    def validate_speaker_labels(cls, value: list[str]) -> list[str]:
        normalized = [label.strip() for label in value]
        if any(not label for label in normalized):
            raise ValueError("speaker labels must be nonempty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("speaker labels must be unique")
        return normalized


class DurationTranscriptionUsage(BaseModel):
    """Provider usage measured by audio duration."""

    type: Literal["duration"] = "duration"
    seconds: float = Field(ge=0)


class TokenTranscriptionUsage(BaseModel):
    """Provider usage measured by tokens."""

    type: Literal["tokens"] = "tokens"
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


TranscriptionUsage = Annotated[
    DurationTranscriptionUsage | TokenTranscriptionUsage,
    Field(discriminator="type"),
]


class TranscriptionMetadata(BaseModel):
    """Persisted `metadata/transcription.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    created_at_utc: datetime
    status: TranscriptionStatus = "transcription_completed"
    provider: TranscriptionProviderMetadata
    input: TranscriptionInputMetadata
    transcript: TranscriptionArtifactsMetadata
    usage: TranscriptionUsage | None = None

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


class TranscriptionResult(BaseModel):
    """Runtime result for local transcription service calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    raw_text_path: Path
    raw_json_path: Path
    transcription_metadata_path: Path
    transcript: RawTranscript
    metadata: TranscriptionMetadata
    reused_existing: bool


def render_raw_text(transcript: RawTranscript) -> str:
    """Render deterministic raw text from canonical segments."""

    if transcript.segments:
        lines = []
        for segment in transcript.segments:
            start = _format_timestamp(segment.start_seconds)
            end = _format_timestamp(segment.end_seconds)
            lines.append(
                f"[{start}-{end}] Speaker {segment.speaker_label}: {segment.text}"
            )
        return "\n".join(lines) + "\n"

    if transcript.text:
        return transcript.text + "\n"
    return "\n"


def _format_timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    total_seconds, millis = divmod(milliseconds, 1000)
    minutes, secs = divmod(total_seconds, 60)
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{mins:02d}:{secs:02d}.{millis:03d}"
    return f"{mins:02d}:{secs:02d}.{millis:03d}"


def _validate_package_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("path must be a safe package-relative path")
