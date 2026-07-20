"""Provider boundary for transcript cleanup."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from backend.app.models.cleanup import CleanupUsage


class CleanupProviderSegment(BaseModel):
    """Trusted raw transcript segment sent to the cleanup provider as data."""

    segment_id: str
    speaker_label: str
    start_seconds: float
    end_seconds: float
    text: str


class CleanupProviderRequest(BaseModel):
    """Provider-independent cleanup request."""

    meeting_id: str
    model: str
    prompt_version: str
    response_format_name: str
    batch_index: int = Field(ge=1)
    batch_count: int = Field(ge=1)
    max_output_tokens: int = Field(gt=0)
    segments: list[CleanupProviderSegment]


class CleanupProviderSegmentResult(BaseModel):
    """Provider-independent cleaned segment text."""

    segment_id: str
    cleaned_text: str


class CleanupProviderResult(BaseModel):
    """Provider-independent cleanup response."""

    segments: list[CleanupProviderSegmentResult]
    usage: CleanupUsage | None = None


class CleanupProvider(Protocol):
    """Protocol implemented by concrete cleanup providers."""

    def clean_batch(self, request: CleanupProviderRequest) -> CleanupProviderResult:
        """Clean one contiguous transcript segment batch."""
