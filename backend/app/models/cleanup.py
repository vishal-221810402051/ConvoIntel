"""Typed models for canonical transcript cleanup artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.config import CLEANUP_MODEL
from backend.app.models.normalization import NORMALIZED_AUDIO_RELATIVE_PATH
from backend.app.models.transcription import (
    TRANSCRIPTION_METADATA_RELATIVE_PATH,
    TRANSCRIPT_JSON_RELATIVE_PATH,
    TRANSCRIPT_TEXT_RELATIVE_PATH,
)

CLEANUP_PROVIDER_NAME = "openai"
CLEANUP_RESPONSE_FORMAT_NAME = "convointel_transcript_cleanup_batch_v1"
CLEANUP_PROMPT_VERSION = "convointel-transcript-cleanup-v1"
CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH = "transcript/cleaned.json"
CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH = "transcript/cleaned.txt"
CLEANUP_METADATA_RELATIVE_PATH = "metadata/cleanup.json"
CLEANUP_STATUS = "cleanup_completed"
SEGMENT_ORDER_TOLERANCE_SECONDS = 0.001

CleanupStatus = Literal["cleanup_completed"]


class CleanedTranscriptSegment(BaseModel):
    """A cleaned transcript segment with trusted Phase 4 structure preserved."""

    segment_id: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    speaker_label: str = Field(min_length=1)
    raw_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cleaned_text: str
    changed: bool

    @field_validator("segment_id", "speaker_label")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("segment identifiers and speaker labels must be nonempty")
        return normalized

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "CleanedTranscriptSegment":
        if self.end_seconds < self.start_seconds:
            raise ValueError("segment end must not be before start")
        return self


class CleanedTranscript(BaseModel):
    """Provider-independent canonical cleaned transcript."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    source_raw_transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: Literal["convointel-transcript-cleanup-v1"] = CLEANUP_PROMPT_VERSION
    text: str
    duration_seconds: float = Field(ge=0)
    segments: list[CleanedTranscriptSegment]

    @model_validator(mode="after")
    def validate_segments(self) -> "CleanedTranscript":
        seen_ids: set[str] = set()
        previous_start: float | None = None
        previous_end: float | None = None

        for segment in self.segments:
            if segment.segment_id in seen_ids:
                raise ValueError("segment IDs must be unique")
            seen_ids.add(segment.segment_id)

            if previous_start is not None and (
                segment.start_seconds + SEGMENT_ORDER_TOLERANCE_SECONDS
                < previous_start
            ):
                raise ValueError("segments must be ordered by start time")
            if previous_end is not None and (
                segment.start_seconds + SEGMENT_ORDER_TOLERANCE_SECONDS
                < previous_end
            ):
                raise ValueError("segments must not overlap")

            previous_start = segment.start_seconds
            previous_end = segment.end_seconds

        return self

    @property
    def speaker_labels(self) -> list[str]:
        return sorted({segment.speaker_label for segment in self.segments})

    @property
    def changed_segment_count(self) -> int:
        return sum(1 for segment in self.segments if segment.changed)

    @property
    def unchanged_segment_count(self) -> int:
        return len(self.segments) - self.changed_segment_count


class CleanupProviderMetadata(BaseModel):
    """Persisted cleanup provider contract."""

    name: Literal["openai"] = CLEANUP_PROVIDER_NAME
    model: Literal["gpt-5-mini-2025-08-07"] = CLEANUP_MODEL
    response_format_name: Literal[
        "convointel_transcript_cleanup_batch_v1"
    ] = CLEANUP_RESPONSE_FORMAT_NAME
    store: Literal[False] = False
    strict_schema: Literal[True] = True


class CleanupInputMetadata(BaseModel):
    """Raw transcript provenance used for cleanup."""

    raw_text_relative_path: Literal["transcript/raw.txt"] = TRANSCRIPT_TEXT_RELATIVE_PATH
    raw_text_size_bytes: int = Field(gt=0)
    raw_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_structured_relative_path: Literal["transcript/raw.json"] = (
        TRANSCRIPT_JSON_RELATIVE_PATH
    )
    raw_structured_size_bytes: int = Field(gt=0)
    raw_structured_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_segment_count: int = Field(ge=0)
    raw_speaker_labels: list[str]
    transcription_metadata_relative_path: Literal["metadata/transcription.json"] = (
        TRANSCRIPTION_METADATA_RELATIVE_PATH
    )
    transcription_metadata_size_bytes: int = Field(gt=0)
    transcription_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_audio_relative_path: Literal["normalized/audio.wav"] = (
        NORMALIZED_AUDIO_RELATIVE_PATH
    )
    normalized_audio_size_bytes: int = Field(gt=0)
    normalized_audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_metadata_relative_path: Literal["metadata/normalization.json"] = (
        "metadata/normalization.json"
    )
    normalization_metadata_size_bytes: int = Field(gt=0)
    normalization_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "raw_text_relative_path",
        "raw_structured_relative_path",
        "transcription_metadata_relative_path",
        "normalized_audio_relative_path",
        "normalization_metadata_relative_path",
    )
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value

    @field_validator("raw_speaker_labels")
    @classmethod
    def validate_speaker_labels(cls, value: list[str]) -> list[str]:
        normalized = [label.strip() for label in value]
        if any(not label for label in normalized):
            raise ValueError("speaker labels must be nonempty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("speaker labels must be unique")
        return normalized


class CleanupArtifactsMetadata(BaseModel):
    """Published cleaned transcript artifact metadata."""

    text_relative_path: Literal["transcript/cleaned.txt"] = (
        CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH
    )
    text_size_bytes: int = Field(gt=0)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structured_relative_path: Literal["transcript/cleaned.json"] = (
        CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH
    )
    structured_size_bytes: int = Field(gt=0)
    structured_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_count: int = Field(ge=0)
    changed_segment_count: int = Field(ge=0)
    unchanged_segment_count: int = Field(ge=0)

    @field_validator("text_relative_path", "structured_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value

    @model_validator(mode="after")
    def validate_segment_counts(self) -> "CleanupArtifactsMetadata":
        if self.changed_segment_count + self.unchanged_segment_count != self.segment_count:
            raise ValueError("changed and unchanged counts must equal segment count")
        return self


class CleanupBatchingMetadata(BaseModel):
    """Deterministic cleanup batching metadata."""

    max_batch_characters: int = Field(ge=1)
    batch_count: int = Field(ge=0)
    provider_request_count: int = Field(ge=0)


class CleanupUsage(BaseModel):
    """Aggregated token usage reported by the cleanup provider."""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class CleanupMetadata(BaseModel):
    """Persisted `metadata/cleanup.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    created_at_utc: datetime
    status: CleanupStatus = CLEANUP_STATUS
    prompt_version: Literal["convointel-transcript-cleanup-v1"] = (
        CLEANUP_PROMPT_VERSION
    )
    provider: CleanupProviderMetadata
    input: CleanupInputMetadata
    artifacts: CleanupArtifactsMetadata
    batching: CleanupBatchingMetadata
    usage: CleanupUsage | None = None

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


class TranscriptCleanupResult(BaseModel):
    """Runtime result for local transcript cleanup service calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    cleaned_json_path: Path
    cleaned_text_path: Path
    cleanup_metadata_path: Path
    transcript: CleanedTranscript
    metadata: CleanupMetadata
    reused_existing: bool


def render_cleaned_text(transcript: CleanedTranscript) -> str:
    """Render deterministic cleaned text from canonical segments."""

    if transcript.segments:
        lines = []
        for segment in transcript.segments:
            start = _format_timestamp(segment.start_seconds)
            end = _format_timestamp(segment.end_seconds)
            lines.append(
                f"[{start}-{end}] Speaker {segment.speaker_label}: "
                f"{segment.cleaned_text}"
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
