"""Provider boundary for temporal intelligence extraction."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.models.intelligence import IntelligenceUsage
from backend.app.models.temporal import (
    TemporalCategory,
    TemporalConfidence,
    TemporalExpressionType,
    TemporalPrecision,
    TemporalRelatedItemType,
    TemporalResolutionBasis,
    TemporalResolutionStatus,
)

MAX_PROVIDER_TEMPORAL_ITEMS = 1000
WEEKDAY_ORDER = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderTemporalIntelligenceReference(_ProviderModel):
    item_type: TemporalRelatedItemType
    item_id: str = Field(min_length=1, max_length=120)


class ProviderTemporalItem(_ProviderModel):
    expression_text: str = Field(min_length=1, max_length=1000)
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
    utc_offset_minutes: int | None = Field(ge=-14 * 60, le=14 * 60)
    duration_value: float | None
    duration_unit: str | None = Field(max_length=40)
    recurrence_frequency: str | None = Field(max_length=40)
    recurrence_interval: int | None = Field(ge=1, le=10000)
    recurrence_days: list[str]
    evidence_segment_ids: list[str] = Field(min_length=1)
    related_intelligence_items: list[ProviderTemporalIntelligenceReference]

    @field_validator("recurrence_days")
    @classmethod
    def normalize_recurrence_days(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().casefold() for item in value]
        if any(not item for item in normalized):
            raise ValueError("recurrence_days entries must be nonempty")
        if any(item not in WEEKDAY_ORDER for item in normalized):
            raise ValueError("recurrence_days entries must be weekday names")
        if len(set(normalized)) != len(normalized):
            raise ValueError("recurrence_days entries must be unique")
        if normalized != sorted(normalized, key=WEEKDAY_ORDER.__getitem__):
            raise ValueError("recurrence_days entries must be weekday ordered")
        return normalized


class ProviderTemporalResponse(_ProviderModel):
    items: list[ProviderTemporalItem] = Field(max_length=MAX_PROVIDER_TEMPORAL_ITEMS)


class TemporalProviderRequest(BaseModel):
    """Provider-independent temporal extraction request."""

    meeting_id: str
    model: str
    prompt_version: str
    response_schema_name: str
    reasoning_effort: str
    max_output_tokens: int = Field(gt=0)
    max_items: int = Field(ge=1, le=1000)
    input_character_count: int = Field(ge=0)
    temporal_payload_json: str


class TemporalProviderResult(BaseModel):
    """Provider-independent temporal extraction response."""

    temporal: ProviderTemporalResponse
    usage: IntelligenceUsage | None = None


class TemporalProvider(Protocol):
    """Protocol implemented by concrete temporal providers."""

    def extract(
        self,
        request: TemporalProviderRequest,
    ) -> TemporalProviderResult:
        """Extract temporal intelligence from one validated meeting package."""
