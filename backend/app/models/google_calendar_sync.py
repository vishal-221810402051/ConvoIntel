"""Typed models for explicitly approved Google Calendar sync artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.models.calendar_recommendation import (
    CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH,
    CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH,
)

GOOGLE_CALENDAR_SYNC_VERSION = "convointel-google-calendar-sync-v1"
GOOGLE_CALENDAR_SYNC_STATUS = "calendar_sync_completed"
GOOGLE_CALENDAR_SYNC_RELATIVE_DIR = "calendar/sync"
GOOGLE_CALENDAR_METADATA_RELATIVE_DIR = "metadata/calendar_sync"
GOOGLE_CALENDAR_ATTEMPTS_RELATIVE_DIR = "metadata/calendar_sync_attempts"
MEETING_ID_PATTERN_TEXT = r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$"
RECOMMENDATION_ID_PATTERN_TEXT = r"^calendar_rec_\d{3}$"
SAFE_GOOGLE_EVENT_ID_PATTERN_TEXT = r"^[a-v0-9]{5,1024}$"

CalendarSyncOperation = Literal["created", "reused_existing"]
CalendarSyncAttemptOutcome = Literal[
    "failed",
    "remote_created_local_persistence_failed",
    "conflict",
]


class CalendarSyncApproval(BaseModel):
    """Runtime-only explicit approval for a single recommendation."""

    model_config = ConfigDict(strict=True)

    recommendation_id: str = Field(pattern=RECOMMENDATION_ID_PATTERN_TEXT)
    confirmed: Literal[True]
    source: Literal["explicit_runtime"] = "explicit_runtime"


class CalendarSyncApprovalRecord(BaseModel):
    """Persisted safe approval audit data."""

    recommendation_id: str = Field(pattern=RECOMMENDATION_ID_PATTERN_TEXT)
    confirmed: Literal[True]
    source: Literal["explicit_runtime"]
    approved_at_utc: datetime

    @field_validator("approved_at_utc")
    @classmethod
    def validate_approved_at_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approved_at_utc must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_serializer("approved_at_utc")
    def serialize_approved_at_utc(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds",
        ).replace("+00:00", "Z")


class GoogleCalendarRemoteEvent(BaseModel):
    """Safe subset of a Google Calendar event."""

    event_id: str = Field(min_length=1)
    html_link: str | None
    status: str | None
    summary: str | None
    start: dict[str, Any] | None
    end: dict[str, Any] | None
    recurrence: list[str] | None
    private_extended_properties: dict[str, str]

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("event_id must not be empty")
        return normalized

    @field_validator("private_extended_properties")
    @classmethod
    def validate_private_properties(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(key): str(item) for key, item in value.items()}


class CalendarSyncTarget(BaseModel):
    """Target calendar metadata."""

    provider: Literal["google_calendar"] = "google_calendar"
    calendar_id: str = Field(min_length=1)


class CalendarSyncSource(BaseModel):
    """Phase 8 source provenance."""

    recommendations_relative_path: Literal["calendar/recommendations.json"] = (
        CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH
    )
    recommendations_size_bytes: int = Field(gt=0)
    recommendations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommendations_metadata_relative_path: Literal[
        "metadata/calendar_recommendations.json"
    ] = CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH
    recommendations_metadata_size_bytes: int = Field(gt=0)
    recommendations_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "recommendations_relative_path",
        "recommendations_metadata_relative_path",
    )
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class CalendarSyncArtifact(BaseModel):
    """Persisted `calendar/sync/<recommendation_id>.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=MEETING_ID_PATTERN_TEXT)
    recommendation_id: str = Field(pattern=RECOMMENDATION_ID_PATTERN_TEXT)
    source: CalendarSyncSource
    sync_version: Literal["convointel-google-calendar-sync-v1"] = (
        GOOGLE_CALENDAR_SYNC_VERSION
    )
    approval: CalendarSyncApprovalRecord
    target: CalendarSyncTarget
    operation: CalendarSyncOperation
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    remote_event: GoogleCalendarRemoteEvent

    @model_validator(mode="after")
    def validate_ids(self) -> "CalendarSyncArtifact":
        if self.approval.recommendation_id != self.recommendation_id:
            raise ValueError("approval recommendation ID must match artifact")
        return self


class CalendarSyncOutputMetadata(BaseModel):
    """Phase 9 output provenance."""

    sync_relative_path: str
    sync_size_bytes: int = Field(gt=0)
    sync_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    google_event_id: str = Field(pattern=SAFE_GOOGLE_EVENT_ID_PATTERN_TEXT)
    operation: CalendarSyncOperation

    @field_validator("sync_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class CalendarSyncValidationMetadata(BaseModel):
    """Local validation flags for Phase 9 processing."""

    upstream_validation_passed: Literal[True] = True
    recommendation_eligibility_passed: Literal[True] = True
    explicit_approval_passed: Literal[True] = True
    payload_validation_passed: Literal[True] = True
    remote_provenance_validation_passed: Literal[True] = True
    local_publication_passed: Literal[True] = True


class CalendarSyncMetadata(BaseModel):
    """Persisted `metadata/calendar_sync/<recommendation_id>.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=MEETING_ID_PATTERN_TEXT)
    created_at_utc: datetime
    status: Literal["calendar_sync_completed"] = GOOGLE_CALENDAR_SYNC_STATUS
    sync_version: Literal["convointel-google-calendar-sync-v1"] = (
        GOOGLE_CALENDAR_SYNC_VERSION
    )
    recommendation_id: str = Field(pattern=RECOMMENDATION_ID_PATTERN_TEXT)
    approval_source: Literal["explicit_runtime"]
    target: CalendarSyncTarget
    source: CalendarSyncSource
    output: CalendarSyncOutputMetadata
    validation: CalendarSyncValidationMetadata

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


class CalendarSyncAttemptRecord(BaseModel):
    """Best-effort safe failure record for Phase 9 attempts."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=MEETING_ID_PATTERN_TEXT)
    recommendation_id: str = Field(pattern=RECOMMENDATION_ID_PATTERN_TEXT)
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at_utc: datetime
    sync_version: Literal["convointel-google-calendar-sync-v1"] = (
        GOOGLE_CALENDAR_SYNC_VERSION
    )
    calendar_id: str = Field(min_length=1)
    google_event_id: str = Field(pattern=SAFE_GOOGLE_EVENT_ID_PATTERN_TEXT)
    outcome: CalendarSyncAttemptOutcome
    operation: Literal["get", "insert", "local_persist", "local_state"]
    failure_type: str = Field(min_length=1)

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


class CalendarSyncResult(BaseModel):
    """Runtime result for Phase 9 service calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    sync_json_path: Path
    sync_metadata_path: Path
    sync: CalendarSyncArtifact
    metadata: CalendarSyncMetadata
    remote_event: GoogleCalendarRemoteEvent
    operation: CalendarSyncOperation
    reused_existing: bool


def sync_relative_path(recommendation_id: str) -> str:
    """Return the package-relative success artifact path."""

    return f"{GOOGLE_CALENDAR_SYNC_RELATIVE_DIR}/{recommendation_id}.json"


def sync_metadata_relative_path(recommendation_id: str) -> str:
    """Return the package-relative metadata path."""

    return f"{GOOGLE_CALENDAR_METADATA_RELATIVE_DIR}/{recommendation_id}.json"


def attempt_relative_path(recommendation_id: str, attempt_id: str) -> str:
    """Return the package-relative attempt metadata path."""

    return (
        f"{GOOGLE_CALENDAR_ATTEMPTS_RELATIVE_DIR}/"
        f"{recommendation_id}/{attempt_id}.json"
    )


def _validate_package_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("path must be a safe package-relative path")
