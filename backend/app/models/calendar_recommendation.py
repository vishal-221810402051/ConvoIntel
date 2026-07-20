"""Typed models for deterministic calendar recommendation artifacts."""

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

from backend.app.models.intelligence import INTELLIGENCE_METADATA_RELATIVE_PATH
from backend.app.models.temporal import (
    TEMPORAL_JSON_RELATIVE_PATH,
    TEMPORAL_METADATA_RELATIVE_PATH,
    TemporalRelatedItemType,
)

CALENDAR_RECOMMENDATION_GENERATOR_VERSION = (
    "convointel-calendar-recommendations-v1"
)
CALENDAR_RECOMMENDATION_GENERATOR_NAME = (
    "convointel-calendar-recommendation-engine"
)
CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH = "calendar/recommendations.json"
CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH = (
    "metadata/calendar_recommendations.json"
)
CALENDAR_RECOMMENDATION_STATUS = "calendar_recommendations_completed"
CALENDAR_RECOMMENDATION_MODE = "deterministic_local"

CalendarRecommendationType = Literal[
    "event",
    "deadline",
    "milestone",
    "recurring_event",
    "reminder_request",
]
CalendarScheduleShape = Literal[
    "all_day",
    "timed",
    "point_in_time",
    "recurring",
    "unscheduled",
]
CalendarReadinessStatus = Literal["ready", "needs_review", "blocked"]
CalendarReviewReason = Literal[
    "ambiguous_temporal",
    "unresolved_temporal",
    "missing_start_date",
    "missing_start_time",
    "missing_end",
    "missing_timezone",
    "no_related_intelligence",
    "recurrence_missing_anchor",
    "recurrence_missing_time",
    "reminder_trigger_unresolved",
    "partial_temporal_information",
]
CalendarBlockingReason = Literal[
    "conflicting_temporal_information",
    "incompatible_temporal_components",
    "multiple_distinct_schedules",
    "invalid_schedule_shape",
]
CalendarInformationalFlag = Literal[
    "duplicate_sources_merged",
    "end_derived_from_duration",
    "date_only_candidate",
    "standalone_temporal_candidate",
    "multiple_evidence_segments",
]
CalendarExclusionReason = Literal[
    "standalone_duration",
    "unsupported_temporal_category",
    "insufficient_calendar_semantics",
    "duplicate_absorbed",
    "non_actionable_temporal_reference",
]


class CalendarRecurrence(BaseModel):
    """Descriptive recurrence copied from trusted Phase 7 output."""

    frequency: str | None
    interval: int | None = Field(default=None, ge=1)
    days: list[str]


class CalendarSchedule(BaseModel):
    """Canonical schedule shape for a recommendation candidate."""

    shape: CalendarScheduleShape
    all_day: bool | None
    start_date: str | None
    start_time: str | None
    end_date: str | None
    end_time: str | None
    timezone_name: str | None
    start_datetime_utc: datetime | None
    end_datetime_utc: datetime | None
    duration_minutes: int | None = Field(default=None, ge=0)
    recurrence: CalendarRecurrence | None
    reminder_expression_text: str | None

    @field_serializer("start_datetime_utc", "end_datetime_utc")
    def serialize_utc_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds",
        ).replace("+00:00", "Z")

    @model_validator(mode="after")
    def validate_shape(self) -> "CalendarSchedule":
        if self.shape == "unscheduled":
            if self.all_day is not None:
                raise ValueError("unscheduled calendars must use null all_day")
            return self
        if self.all_day is None:
            raise ValueError("scheduled shapes require all_day to be true or false")
        if self.shape == "all_day" and self.all_day is not True:
            raise ValueError("all_day shape requires all_day=true")
        if self.shape in {"timed", "point_in_time", "recurring"} and self.all_day is not False:
            raise ValueError("timed, point-in-time, and recurring shapes require all_day=false")
        if self.shape == "all_day" and (self.start_time or self.end_time):
            raise ValueError("all-day schedules must not contain local times")
        if self.shape == "timed":
            if not (self.start_date and self.start_time and self.end_date and self.end_time):
                raise ValueError("timed schedules require start and end date-times")
        if self.shape == "point_in_time" and not self.start_date:
            raise ValueError("point-in-time schedules require a start date")
        if self.shape == "recurring" and self.recurrence is None:
            raise ValueError("recurring schedules require recurrence details")
        return self


class CalendarEvidenceReference(BaseModel):
    """Transcript evidence reference copied from trusted Phase 7 output."""

    segment_id: str = Field(min_length=1)
    speaker_label: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    cleaned_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "CalendarEvidenceReference":
        if self.end_seconds < self.start_seconds:
            raise ValueError("evidence end must not be before start")
        return self


class CalendarIntelligenceReference(BaseModel):
    """Reference to an existing Phase 6 intelligence item."""

    item_type: TemporalRelatedItemType
    item_id: str = Field(min_length=1)


class CalendarRecommendation(BaseModel):
    """Reviewable deterministic calendar recommendation."""

    recommendation_id: str = Field(pattern=r"^calendar_rec_\d{3}$")
    recommendation_type: CalendarRecommendationType
    readiness_status: CalendarReadinessStatus
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=10000)
    schedule: CalendarSchedule
    source_temporal_ids: list[str] = Field(min_length=1)
    related_intelligence_items: list[CalendarIntelligenceReference]
    evidence: list[CalendarEvidenceReference]
    review_reasons: list[CalendarReviewReason]
    blocking_reasons: list[CalendarBlockingReason]
    informational_flags: list[CalendarInformationalFlag]
    deduplication_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    merged_source_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_recommendation(self) -> "CalendarRecommendation":
        if self.readiness_status == "ready":
            if self.review_reasons or self.blocking_reasons:
                raise ValueError("ready recommendations must not have issue reasons")
        if self.readiness_status == "needs_review":
            if not self.review_reasons:
                raise ValueError("needs-review recommendations require review reasons")
            if self.blocking_reasons:
                raise ValueError("needs-review recommendations must not have blocking reasons")
        if self.readiness_status == "blocked" and not self.blocking_reasons:
            raise ValueError("blocked recommendations require blocking reasons")
        _validate_unique_ordered(self.source_temporal_ids, "source_temporal_ids")
        _validate_unique_ordered(
            [
                f"{item.item_type}:{item.item_id}"
                for item in self.related_intelligence_items
            ],
            "related_intelligence_items",
        )
        _validate_unique_ordered(
            [item.segment_id for item in self.evidence],
            "evidence",
        )
        if self.recommendation_type == "event" and self.schedule.shape == "point_in_time":
            raise ValueError("events must not use point-in-time schedules")
        if self.recommendation_type in {"deadline", "milestone"} and self.schedule.shape == "timed":
            raise ValueError("deadlines and milestones must not require event duration")
        if self.recommendation_type == "recurring_event" and self.schedule.shape != "recurring":
            if self.schedule.shape != "unscheduled":
                raise ValueError("recurring recommendations require recurring schedules")
        if self.recommendation_type == "reminder_request":
            if self.schedule.reminder_expression_text is None:
                raise ValueError("reminder recommendations preserve the reminder expression")
        return self


class CalendarExclusion(BaseModel):
    """Temporal source that intentionally did not become a recommendation."""

    exclusion_id: str = Field(pattern=r"^calendar_exclusion_\d{3}$")
    source_temporal_ids: list[str] = Field(min_length=1)
    reason: CalendarExclusionReason
    description: str = Field(min_length=1)
    evidence: list[CalendarEvidenceReference]

    @model_validator(mode="after")
    def validate_exclusion(self) -> "CalendarExclusion":
        _validate_unique_ordered(self.source_temporal_ids, "source_temporal_ids")
        _validate_unique_ordered([item.segment_id for item in self.evidence], "evidence")
        return self


class CalendarRecommendationArtifact(BaseModel):
    """Canonical persisted Phase 8 recommendation artifact."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    source_intelligence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_intelligence_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_temporal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_temporal_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: Literal[
        "convointel-calendar-recommendations-v1"
    ] = CALENDAR_RECOMMENDATION_GENERATOR_VERSION
    recommendations: list[CalendarRecommendation]
    exclusions: list[CalendarExclusion]

    @model_validator(mode="after")
    def validate_ids(self) -> "CalendarRecommendationArtifact":
        expected_recommendations = [
            f"calendar_rec_{index:03d}"
            for index in range(1, len(self.recommendations) + 1)
        ]
        if [item.recommendation_id for item in self.recommendations] != expected_recommendations:
            raise ValueError("recommendation IDs are not sequential")
        expected_exclusions = [
            f"calendar_exclusion_{index:03d}"
            for index in range(1, len(self.exclusions) + 1)
        ]
        if [item.exclusion_id for item in self.exclusions] != expected_exclusions:
            raise ValueError("exclusion IDs are not sequential")
        return self


class CalendarGeneratorMetadata(BaseModel):
    """Deterministic local generator contract."""

    name: Literal["convointel-calendar-recommendation-engine"] = (
        CALENDAR_RECOMMENDATION_GENERATOR_NAME
    )
    version: Literal["convointel-calendar-recommendations-v1"] = (
        CALENDAR_RECOMMENDATION_GENERATOR_VERSION
    )
    mode: Literal["deterministic_local"] = CALENDAR_RECOMMENDATION_MODE
    network_access: Literal[False] = False
    provider_request_count: Literal[0] = 0


class CalendarInputMetadata(BaseModel):
    """Phase 6 and Phase 7 provenance used by Phase 8."""

    intelligence_relative_path: Literal["intelligence/decision_intelligence.json"] = (
        "intelligence/decision_intelligence.json"
    )
    intelligence_size_bytes: int = Field(gt=0)
    intelligence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intelligence_metadata_relative_path: Literal["metadata/intelligence.json"] = (
        INTELLIGENCE_METADATA_RELATIVE_PATH
    )
    intelligence_metadata_size_bytes: int = Field(gt=0)
    intelligence_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temporal_relative_path: Literal["temporal/temporal_intelligence.json"] = (
        TEMPORAL_JSON_RELATIVE_PATH
    )
    temporal_size_bytes: int = Field(gt=0)
    temporal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temporal_metadata_relative_path: Literal["metadata/temporal.json"] = (
        TEMPORAL_METADATA_RELATIVE_PATH
    )
    temporal_metadata_size_bytes: int = Field(gt=0)
    temporal_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temporal_item_count: int = Field(ge=0)
    temporal_gap_count: int = Field(ge=0)

    @field_validator(
        "intelligence_relative_path",
        "intelligence_metadata_relative_path",
        "temporal_relative_path",
        "temporal_metadata_relative_path",
    )
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class CalendarOutputMetadata(BaseModel):
    """Phase 8 output provenance and counts."""

    recommendations_relative_path: Literal["calendar/recommendations.json"] = (
        CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH
    )
    recommendations_size_bytes: int = Field(gt=0)
    recommendations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recommendation_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    needs_review_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    merged_source_count: int = Field(ge=0)

    @field_validator("recommendations_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value

    @model_validator(mode="after")
    def validate_counts(self) -> "CalendarOutputMetadata":
        if (
            self.ready_count + self.needs_review_count + self.blocked_count
            != self.recommendation_count
        ):
            raise ValueError("readiness counts must equal recommendation count")
        return self


class CalendarProcessingMetadata(BaseModel):
    """Local validation flags for Phase 8 processing."""

    input_validation_passed: Literal[True] = True
    candidate_grouping_passed: Literal[True] = True
    schedule_validation_passed: Literal[True] = True
    readiness_validation_passed: Literal[True] = True
    deduplication_validation_passed: Literal[True] = True
    exclusion_validation_passed: Literal[True] = True


class CalendarRecommendationMetadata(BaseModel):
    """Persisted `metadata/calendar_recommendations.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    created_at_utc: datetime
    status: Literal["calendar_recommendations_completed"] = (
        CALENDAR_RECOMMENDATION_STATUS
    )
    generator: CalendarGeneratorMetadata
    input: CalendarInputMetadata
    output: CalendarOutputMetadata
    processing: CalendarProcessingMetadata

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


class CalendarRecommendationResult(BaseModel):
    """Runtime result for Phase 8 service calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    recommendations_json_path: Path
    recommendations_metadata_path: Path
    recommendations: CalendarRecommendationArtifact
    metadata: CalendarRecommendationMetadata
    reused_existing: bool


def calendar_recommendation_counts(
    artifact: CalendarRecommendationArtifact,
) -> dict[str, int]:
    """Return derived counts for metadata validation."""

    ready_count = sum(
        1 for item in artifact.recommendations if item.readiness_status == "ready"
    )
    needs_review_count = sum(
        1
        for item in artifact.recommendations
        if item.readiness_status == "needs_review"
    )
    blocked_count = sum(
        1 for item in artifact.recommendations if item.readiness_status == "blocked"
    )
    merged_source_count = sum(
        max(0, item.merged_source_count - 1)
        for item in artifact.recommendations
    )
    return {
        "recommendation_count": len(artifact.recommendations),
        "ready_count": ready_count,
        "needs_review_count": needs_review_count,
        "blocked_count": blocked_count,
        "exclusion_count": len(artifact.exclusions),
        "merged_source_count": merged_source_count,
    }


def _validate_unique_ordered(values: list[str], field_name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must contain unique values")


def _validate_package_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("path must be a safe package-relative path")
