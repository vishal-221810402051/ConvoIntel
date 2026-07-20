"""Provider boundary for diarized raw transcription."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from backend.app.models.transcription import (
    TRANSCRIPTION_CHUNKING_STRATEGY,
    TRANSCRIPTION_RESPONSE_FORMAT,
    TranscriptionUsage,
)


class TranscriptionProviderRequest(BaseModel):
    """Provider-independent transcription request."""

    meeting_id: str
    audio_path: Path
    model: str
    response_format: str = TRANSCRIPTION_RESPONSE_FORMAT
    chunking_strategy: str = TRANSCRIPTION_CHUNKING_STRATEGY
    language: str | None = None


class TranscriptionProviderSegment(BaseModel):
    """Provider segment mapped before canonical publication."""

    segment_id: str | None = None
    start_seconds: float
    end_seconds: float
    speaker_label: str
    text: str


class TranscriptionProviderResult(BaseModel):
    """Provider-independent transcription response."""

    text: str
    duration_seconds: float = Field(ge=0)
    segments: list[TranscriptionProviderSegment]
    usage: TranscriptionUsage | None = None


class TranscriptionProvider(Protocol):
    """Protocol implemented by concrete transcription providers."""

    def transcribe(
        self,
        request: TranscriptionProviderRequest,
    ) -> TranscriptionProviderResult:
        """Transcribe a normalized audio file."""
