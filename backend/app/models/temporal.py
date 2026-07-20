"""Typed models for canonical temporal intelligence artifacts."""

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

from backend.app.config import TEMPORAL_MODEL
from backend.app.models.cleanup import CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH
from backend.app.models.intelligence import (
    INTELLIGENCE_JSON_RELATIVE_PATH,
    INTELLIGENCE_METADATA_RELATIVE_PATH,
    IntelligenceUsage,
)

TEMPORAL_PROVIDER_NAME = "openai"
TEMPORAL_ENDPOINT = "responses"
TEMPORAL_PROMPT_VERSION = "convointel-temporal-intelligence-v1"
TEMPORAL_RESPONSE_SCHEMA_NAME = "convointel_temporal_intelligence_v1"
TEMPORAL_REASONING_EFFORT = "low"
TEMPORAL_JSON_RELATIVE_PATH = "temporal/temporal_intelligence.json"
TEMPORAL_METADATA_RELATIVE_PATH = "metadata/temporal.json"
TEMPORAL_STATUS = "temporal_completed"
TEMPORAL_REFERENCE_SOURCE = "explicit_runtime"

TemporalCategory = Literal[
    "date_reference",
    "time_reference",
    "datetime_reference",
    "deadline",
    "milestone",
    "duration",
    "time_window",
    "recurrence",
    "reminder_request",
    "other_temporal",
]
TemporalExpressionType = Literal[
    "absolute",
    "relative",
    "deictic",
    "duration",
    "recurring",
    "range",
    "vague",
    "unknown",
]
TemporalResolutionStatus = Literal[
    "resolved_exact",
    "resolved_relative",
    "ambiguous",
    "unresolved",
]
TemporalResolutionBasis = Literal[
    "explicit_text",
    "reference_datetime",
    "contextual_inference",
    "insufficient_information",
]
TemporalPrecision = Literal[
    "year",
    "quarter",
    "month",
    "week",
    "date",
    "time",
    "datetime",
    "range",
    "duration",
    "recurrence",
    "unknown",
]
TemporalConfidence = Literal["high", "medium", "low"]
TemporalGapKind = Literal[
    "missing_deadline",
    "ambiguous_deadline",
    "unresolved_expression",
    "missing_reference",
    "conflicting_temporal_information",
]
TemporalRelatedItemType = Literal[
    "decision",
    "action_item",
    "commitment",
    "follow_up",
    "missing_information",
    "gap",
]


class TemporalReference(BaseModel):
    """Trusted runtime reference supplied explicitly by the caller."""

    source: Literal["explicit_runtime"] = TEMPORAL_REFERENCE_SOURCE
    reference_datetime_local: datetime
    reference_datetime_utc: datetime
    timezone_name: str = Field(min_length=1)
    utc_offset_minutes: int = Field(ge=-14 * 60, le=14 * 60)

    @field_validator("reference_datetime_local", "reference_datetime_utc")
    @classmethod
    def validate_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference datetimes must be timezone-aware")
        return value

    @field_validator("reference_datetime_utc")
    @classmethod
    def validate_utc_datetime(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @field_serializer("reference_datetime_local")
    def serialize_reference_datetime_local(self, value: datetime) -> str:
        return value.isoformat(timespec="microseconds")

    @field_serializer("reference_datetime_utc")
    def serialize_reference_datetime_utc(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds",
        ).replace("+00:00", "Z")


class TemporalEvidenceReference(BaseModel):
    """Trusted local evidence reference resolved from the cleaned transcript."""

    segment_id: str = Field(min_length=1)
    speaker_label: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    cleaned_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "TemporalEvidenceReference":
        if self.end_seconds < self.start_seconds:
            raise ValueError("evidence end must not be before start")
        return self


class TemporalIntelligenceReference(BaseModel):
    """Reference to an existing Phase 6 intelligence item."""

    item_type: TemporalRelatedItemType
    item_id: str = Field(min_length=1)


class TemporalItem(BaseModel):
    """Canonical locally normalized temporal intelligence item."""

    temporal_id: str = Field(pattern=r"^temporal_\d{3}$")
    expression_text: str = Field(min_length=1)
    category: TemporalCategory
    expression_type: TemporalExpressionType
    resolution_status: TemporalResolutionStatus
    resolution_basis: TemporalResolutionBasis
    precision: TemporalPrecision
    confidence: TemporalConfidence
    start_date: str | None
    start_time: str | None
    end_date: str | None
    end_time: str | None
    timezone_name: str | None
    utc_offset_minutes: int | None = Field(default=None, ge=-14 * 60, le=14 * 60)
    start_datetime_utc: datetime | None
    end_datetime_utc: datetime | None
    duration_value: float | None
    duration_unit: str | None
    duration_seconds: int | None = Field(default=None, ge=0)
    recurrence_frequency: str | None
    recurrence_interval: int | None = Field(default=None, ge=1)
    recurrence_days: list[str]
    evidence: list[TemporalEvidenceReference] = Field(min_length=1)
    related_intelligence_items: list[TemporalIntelligenceReference]

    @field_serializer("start_datetime_utc", "end_datetime_utc")
    def serialize_utc_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds",
        ).replace("+00:00", "Z")


class TemporalGap(BaseModel):
    """Locally derived temporal gap."""

    gap_id: str = Field(pattern=r"^gap_\d{3}$")
    kind: TemporalGapKind
    description: str = Field(min_length=1)
    related_temporal_id: str | None = Field(default=None, pattern=r"^temporal_\d{3}$")
    related_intelligence_item: TemporalIntelligenceReference | None
    evidence: list[TemporalEvidenceReference]


class TemporalCategoryCounts(BaseModel):
    date_reference: int = Field(ge=0)
    time_reference: int = Field(ge=0)
    datetime_reference: int = Field(ge=0)
    deadline: int = Field(ge=0)
    milestone: int = Field(ge=0)
    duration: int = Field(ge=0)
    time_window: int = Field(ge=0)
    recurrence: int = Field(ge=0)
    reminder_request: int = Field(ge=0)
    other_temporal: int = Field(ge=0)
    gaps: int = Field(ge=0)


class TemporalIntelligenceArtifact(BaseModel):
    """Canonical persisted temporal intelligence artifact."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    source_cleaned_transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_intelligence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_intelligence_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: Literal["convointel-temporal-intelligence-v1"] = (
        TEMPORAL_PROMPT_VERSION
    )
    temporal_reference: TemporalReference | None
    items: list[TemporalItem]
    gaps: list[TemporalGap]

    def category_counts(self) -> TemporalCategoryCounts:
        counts = {category: 0 for category in TemporalCategory.__args__}
        for item in self.items:
            counts[item.category] += 1
        return TemporalCategoryCounts(**counts, gaps=len(self.gaps))


class TemporalProviderMetadata(BaseModel):
    name: Literal["openai"] = TEMPORAL_PROVIDER_NAME
    endpoint: Literal["responses"] = TEMPORAL_ENDPOINT
    model: Literal["gpt-5-mini-2025-08-07"] = TEMPORAL_MODEL
    prompt_version: Literal["convointel-temporal-intelligence-v1"] = (
        TEMPORAL_PROMPT_VERSION
    )
    response_format: Literal["json_schema"] = "json_schema"
    response_schema: Literal["convointel_temporal_intelligence_v1"] = (
        TEMPORAL_RESPONSE_SCHEMA_NAME
    )
    strict_schema: Literal[True] = True
    store: Literal[False] = False
    reasoning_effort: Literal["low"] = TEMPORAL_REASONING_EFFORT


class TemporalInputMetadata(BaseModel):
    cleaned_json_relative_path: Literal["transcript/cleaned.json"] = (
        CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH
    )
    cleaned_json_size_bytes: int = Field(gt=0)
    cleaned_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intelligence_relative_path: Literal["intelligence/decision_intelligence.json"] = (
        INTELLIGENCE_JSON_RELATIVE_PATH
    )
    intelligence_size_bytes: int = Field(gt=0)
    intelligence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intelligence_metadata_relative_path: Literal["metadata/intelligence.json"] = (
        INTELLIGENCE_METADATA_RELATIVE_PATH
    )
    intelligence_metadata_size_bytes: int = Field(gt=0)
    intelligence_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_count: int = Field(ge=0)
    speaker_labels: list[str]
    temporal_reference: TemporalReference | None

    @field_validator(
        "cleaned_json_relative_path",
        "intelligence_relative_path",
        "intelligence_metadata_relative_path",
    )
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class TemporalOutputMetadata(BaseModel):
    temporal_relative_path: Literal["temporal/temporal_intelligence.json"] = (
        TEMPORAL_JSON_RELATIVE_PATH
    )
    temporal_size_bytes: int = Field(gt=0)
    temporal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    category_counts: TemporalCategoryCounts

    @field_validator("temporal_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class TemporalProcessingMetadata(BaseModel):
    provider_request_count: int = Field(ge=0)
    input_character_count: int = Field(ge=0)
    max_input_characters: int = Field(ge=1)
    max_items: int = Field(ge=1, le=1000)
    evidence_validation_passed: Literal[True] = True
    intelligence_link_validation_passed: Literal[True] = True
    normalization_validation_passed: Literal[True] = True
    gap_validation_passed: Literal[True] = True


class TemporalMetadata(BaseModel):
    """Persisted `metadata/temporal.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    created_at_utc: datetime
    status: Literal["temporal_completed"] = TEMPORAL_STATUS
    provider: TemporalProviderMetadata
    input: TemporalInputMetadata
    output: TemporalOutputMetadata
    processing: TemporalProcessingMetadata
    usage: IntelligenceUsage | None = None

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


class TemporalIntelligenceResult(BaseModel):
    """Runtime result for temporal intelligence service calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    temporal_json_path: Path
    temporal_metadata_path: Path
    temporal_intelligence: TemporalIntelligenceArtifact
    metadata: TemporalMetadata
    reused_existing: bool


def empty_temporal_intelligence(
    *,
    meeting_id: str,
    cleaned_sha256: str,
    intelligence_sha256: str,
    intelligence_metadata_sha256: str,
    temporal_reference: TemporalReference | None,
) -> TemporalIntelligenceArtifact:
    """Build a valid empty temporal intelligence artifact."""

    return TemporalIntelligenceArtifact(
        meeting_id=meeting_id,
        source_cleaned_transcript_sha256=cleaned_sha256,
        source_intelligence_sha256=intelligence_sha256,
        source_intelligence_metadata_sha256=intelligence_metadata_sha256,
        temporal_reference=temporal_reference,
        items=[],
        gaps=[],
    )


def _validate_package_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("path must be a safe package-relative path")
