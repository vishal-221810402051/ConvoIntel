"""Canonical general decision-intelligence publication service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from backend.app.config import (
    CLEANUP_MODEL,
    INTELLIGENCE_MODEL,
    TRANSCRIPTION_MODEL,
    Settings,
    get_settings,
)
from backend.app.models.cleanup import (
    CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH,
    CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH,
    CLEANUP_METADATA_RELATIVE_PATH,
    CLEANUP_PROMPT_VERSION,
    CLEANUP_RESPONSE_FORMAT_NAME,
    CleanedTranscript,
    CleanedTranscriptSegment,
    CleanupMetadata,
    render_cleaned_text,
)
from backend.app.models.intelligence import (
    INTELLIGENCE_JSON_RELATIVE_PATH,
    INTELLIGENCE_METADATA_RELATIVE_PATH,
    INTELLIGENCE_PROMPT_VERSION,
    INTELLIGENCE_PROVIDER_NAME,
    INTELLIGENCE_REASONING_EFFORT,
    INTELLIGENCE_RESPONSE_SCHEMA_NAME,
    ActionItem,
    Blocker,
    Commitment,
    Decision,
    DecisionIntelligenceArtifact,
    DecisionIntelligenceMetadata,
    DecisionIntelligenceResult,
    Dependency,
    DiscussionArea,
    ExecutiveSummary,
    FollowUp,
    IntelligenceActor,
    IntelligenceCategoryCounts,
    IntelligenceDeadline,
    IntelligenceEvidenceReference,
    IntelligenceGap,
    IntelligenceInputMetadata,
    IntelligenceOutputMetadata,
    IntelligenceProcessingMetadata,
    IntelligenceProviderMetadata,
    IntelligenceUsage,
    KeyOutcome,
    MissingInformation,
    Opportunity,
    Recommendation,
    Risk,
    StakeholderPosition,
    StrategicInsight,
    UnresolvedQuestion,
    empty_decision_intelligence,
)
from backend.app.models.meeting import MeetingManifest
from backend.app.models.normalization import (
    CANONICAL_NORMALIZATION_PROFILE,
    NORMALIZED_AUDIO_RELATIVE_PATH,
    NormalizationMetadata,
)
from backend.app.models.transcription import (
    TRANSCRIPTION_CHUNKING_STRATEGY,
    TRANSCRIPTION_METADATA_RELATIVE_PATH,
    TRANSCRIPTION_PROVIDER_NAME,
    TRANSCRIPTION_RESPONSE_FORMAT,
    TRANSCRIPT_JSON_RELATIVE_PATH,
    TRANSCRIPT_TEXT_RELATIVE_PATH,
    RawTranscript,
    TranscriptionMetadata,
    render_raw_text,
)
from backend.app.services.intelligence.errors import (
    DecisionIntelligenceError,
    IntelligenceActorError,
    IntelligenceDeadlineError,
    IntelligenceEvidenceError,
    IntelligenceInputIntegrityError,
    IntelligenceInputNotFoundError,
    IntelligenceInputTooLargeError,
    IntelligenceMetadataWriteError,
    IntelligenceProviderResponseError,
    IntelligencePublicationError,
    IntelligenceStateError,
)
from backend.app.services.intelligence.openai_provider import OpenAIIntelligenceProvider
from backend.app.services.intelligence.provider import (
    IntelligenceProvider,
    IntelligenceProviderRequest,
    ProviderActionItem,
    ProviderBlocker,
    ProviderCommitment,
    ProviderDecision,
    ProviderDecisionIntelligence,
    ProviderDependency,
    ProviderDiscussionArea,
    ProviderFollowUp,
    ProviderKeyOutcome,
    ProviderMissingInformation,
    ProviderOpportunity,
    ProviderRecommendation,
    ProviderRisk,
    ProviderStakeholderPosition,
    ProviderStrategicInsight,
    ProviderUnresolvedQuestion,
)

logger = logging.getLogger(__name__)

MEETING_ID_PATTERN = re.compile(
    r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$",
)
HASH_BUFFER_SIZE = 1024 * 1024
PROTECTED_TOKEN_PATTERN = re.compile(
    r"https?://[^\s\])>,;]+"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|[$€£¥]\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?%"
    r"|[A-Za-z0-9][A-Za-z0-9._:/-]*\d[A-Za-z0-9._:/%+-]*"
)
TOKEN_EDGE_PUNCTUATION = " \t\r\n.,;!?()[]{}\"'“”‘’"
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class _IntelligenceContext:
    meeting_id: str
    meeting_dir: Path
    manifest: MeetingManifest
    normalization: NormalizationMetadata
    transcription_metadata: TranscriptionMetadata
    cleanup_metadata: CleanupMetadata
    raw_transcript: RawTranscript
    cleaned_transcript: CleanedTranscript
    raw_json_path: Path
    raw_json_size_bytes: int
    raw_json_sha256: str
    raw_text_path: Path
    raw_text_size_bytes: int
    raw_text_sha256: str
    cleaned_json_path: Path
    cleaned_json_size_bytes: int
    cleaned_json_sha256: str
    cleaned_text_path: Path
    cleaned_text_size_bytes: int
    cleaned_text_sha256: str
    cleanup_metadata_path: Path
    cleanup_metadata_size_bytes: int
    cleanup_metadata_sha256: str
    transcription_metadata_path: Path
    transcription_metadata_size_bytes: int
    transcription_metadata_sha256: str
    normalization_metadata_path: Path
    normalization_metadata_size_bytes: int
    normalization_metadata_sha256: str
    normalized_audio_path: Path
    normalized_audio_size_bytes: int
    normalized_audio_sha256: str


class DecisionIntelligenceService:
    """Publish evidence-grounded general meeting decision intelligence."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: IntelligenceProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def analyze_meeting(self, meeting_id: str) -> DecisionIntelligenceResult:
        logger.info("Starting decision intelligence for meeting %s", meeting_id)

        context = self._load_context(meeting_id)
        logger.info(
            "Phase 5 integrity verified for meeting %s with %s segments",
            meeting_id,
            len(context.cleaned_transcript.segments),
        )

        intelligence_path = context.meeting_dir / INTELLIGENCE_JSON_RELATIVE_PATH
        metadata_path = context.meeting_dir / INTELLIGENCE_METADATA_RELATIVE_PATH

        existing_result = self._reuse_existing_if_valid(
            context,
            intelligence_path,
            metadata_path,
        )
        if existing_result is not None:
            logger.info("Reusing existing decision intelligence for meeting %s", meeting_id)
            return existing_result

        staging_dir = (
            context.meeting_dir / ".staging" / f"intelligence_{uuid.uuid4().hex}"
        )

        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_intelligence_path = staging_dir / "decision_intelligence.json"
            staged_metadata_path = staging_dir / "intelligence.json"

            serialized_payload = self._serialize_transcript_payload(context)
            input_character_count = len(serialized_payload)
            logger.info(
                "Decision intelligence input has %s characters for meeting %s",
                input_character_count,
                meeting_id,
            )
            if input_character_count > self._settings.intelligence_max_input_characters:
                raise IntelligenceInputTooLargeError(
                    "Cleaned transcript exceeds the configured intelligence input limit."
                )

            provider_request_count = 0
            usage: IntelligenceUsage | None = None
            if not context.cleaned_transcript.segments:
                intelligence = empty_decision_intelligence(
                    meeting_id=context.meeting_id,
                    cleaned_sha256=context.cleaned_json_sha256,
                    cleanup_metadata_sha256=context.cleanup_metadata_sha256,
                )
            else:
                provider = self._provider or OpenAIIntelligenceProvider(self._settings)
                request = IntelligenceProviderRequest(
                    meeting_id=context.meeting_id,
                    model=self._settings.intelligence_model,
                    prompt_version=INTELLIGENCE_PROMPT_VERSION,
                    response_schema_name=INTELLIGENCE_RESPONSE_SCHEMA_NAME,
                    reasoning_effort=INTELLIGENCE_REASONING_EFFORT,
                    max_output_tokens=self._settings.intelligence_max_output_tokens,
                    max_items_per_category=(
                        self._settings.intelligence_max_items_per_category
                    ),
                    input_character_count=input_character_count,
                    transcript_payload_json=serialized_payload,
                )
                logger.info("Starting intelligence provider request for %s", meeting_id)
                provider_result = provider.analyze(request)
                provider_request_count = 1
                usage = provider_result.usage
                logger.info("Completed intelligence provider request for %s", meeting_id)
                self._validate_provider_intelligence(context, provider_result.intelligence)
                intelligence = self._build_canonical_intelligence(
                    context,
                    provider_result.intelligence,
                )

            self._validate_canonical_intelligence(context, intelligence)
            logger.info(
                "Decision intelligence category counts for %s: %s",
                meeting_id,
                intelligence.category_counts().model_dump(),
            )

            self._write_json_atomically(
                intelligence,
                staged_intelligence_path,
                IntelligencePublicationError,
                "Decision intelligence JSON could not be written.",
            )
            intelligence_size, intelligence_sha = self._inspect_file(
                staged_intelligence_path,
                IntelligencePublicationError,
            )
            metadata = self._build_metadata(
                context,
                intelligence,
                intelligence_size,
                intelligence_sha,
                provider_request_count=provider_request_count,
                input_character_count=input_character_count,
                usage=usage,
            )
            self._write_json_atomically(
                metadata,
                staged_metadata_path,
                IntelligenceMetadataWriteError,
                "Decision intelligence metadata could not be written.",
            )
            self._publish_artifacts(
                staged_intelligence_path,
                intelligence_path,
                staged_metadata_path,
                metadata_path,
            )

            logger.info("Completed decision intelligence for meeting %s", meeting_id)
            return DecisionIntelligenceResult(
                meeting_id=context.meeting_id,
                meeting_dir=context.meeting_dir.resolve(strict=False),
                intelligence_json_path=intelligence_path.resolve(strict=False),
                intelligence_metadata_path=metadata_path.resolve(strict=False),
                intelligence=intelligence,
                metadata=metadata,
                reused_existing=False,
            )
        except DecisionIntelligenceError:
            logger.info("Decision intelligence failed for meeting %s", meeting_id)
            raise
        except OSError as exc:
            logger.info("Decision intelligence failed for meeting %s", meeting_id)
            raise IntelligencePublicationError(
                "Decision intelligence artifacts could not be staged or published."
            ) from exc
        finally:
            self._rollback_staging(staging_dir)

    def _load_context(self, meeting_id: str) -> _IntelligenceContext:
        if not MEETING_ID_PATTERN.fullmatch(meeting_id):
            raise IntelligenceInputIntegrityError("Meeting ID is invalid.")

        meetings_dir = self._settings.meetings_dir
        meeting_dir = (meetings_dir / meeting_id).resolve(strict=False)
        if not self._is_relative_to(meeting_dir, meetings_dir):
            raise IntelligenceInputNotFoundError("Meeting package path is invalid.")
        if not meeting_dir.exists() or not meeting_dir.is_dir():
            raise IntelligenceInputNotFoundError("Meeting package was not found.")

        manifest = self._load_model(
            meeting_dir / "metadata" / "meeting.json",
            MeetingManifest,
            IntelligenceInputNotFoundError,
            "Meeting manifest was not found.",
            "Meeting manifest is invalid for intelligence.",
        )
        normalization_path = meeting_dir / "metadata" / "normalization.json"
        normalization = self._load_model(
            normalization_path,
            NormalizationMetadata,
            IntelligenceInputNotFoundError,
            "Normalization metadata was not found.",
            "Normalization metadata is invalid for intelligence.",
        )
        transcription_path = meeting_dir / TRANSCRIPTION_METADATA_RELATIVE_PATH
        transcription_metadata = self._load_model(
            transcription_path,
            TranscriptionMetadata,
            IntelligenceInputNotFoundError,
            "Transcription metadata was not found.",
            "Transcription metadata is invalid for intelligence.",
        )
        cleanup_metadata_path = meeting_dir / CLEANUP_METADATA_RELATIVE_PATH
        cleanup_metadata = self._load_model(
            cleanup_metadata_path,
            CleanupMetadata,
            IntelligenceInputNotFoundError,
            "Cleanup metadata was not found.",
            "Cleanup metadata is invalid for intelligence.",
        )
        raw_json_path = meeting_dir / TRANSCRIPT_JSON_RELATIVE_PATH
        raw_transcript = self._load_model(
            raw_json_path,
            RawTranscript,
            IntelligenceInputNotFoundError,
            "Raw transcript JSON was not found.",
            "Raw transcript JSON is invalid for intelligence.",
        )
        cleaned_json_path = meeting_dir / CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH
        cleaned_transcript = self._load_model(
            cleaned_json_path,
            CleanedTranscript,
            IntelligenceInputNotFoundError,
            "Cleaned transcript JSON was not found.",
            "Cleaned transcript JSON is invalid for intelligence.",
        )

        if {
            manifest.meeting_id,
            normalization.meeting_id,
            transcription_metadata.meeting_id,
            cleanup_metadata.meeting_id,
            raw_transcript.meeting_id,
            cleaned_transcript.meeting_id,
            meeting_id,
        } != {meeting_id}:
            raise IntelligenceInputIntegrityError(
                "Meeting metadata IDs do not match the requested meeting ID."
            )

        raw_text_path = meeting_dir / TRANSCRIPT_TEXT_RELATIVE_PATH
        cleaned_text_path = meeting_dir / CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH
        normalized_audio_path = self._resolve_package_relative_path(
            meeting_dir,
            normalization.output.relative_path,
            IntelligenceInputIntegrityError,
        )

        raw_json_size, raw_json_sha = self._inspect_file(
            raw_json_path,
            IntelligenceInputIntegrityError,
        )
        raw_text_size, raw_text_sha = self._inspect_file(
            raw_text_path,
            IntelligenceInputIntegrityError,
        )
        cleaned_json_size, cleaned_json_sha = self._inspect_file(
            cleaned_json_path,
            IntelligenceInputIntegrityError,
        )
        cleaned_text_size, cleaned_text_sha = self._inspect_file(
            cleaned_text_path,
            IntelligenceInputIntegrityError,
        )
        cleanup_metadata_size, cleanup_metadata_sha = self._inspect_file(
            cleanup_metadata_path,
            IntelligenceInputIntegrityError,
        )
        transcription_size, transcription_sha = self._inspect_file(
            transcription_path,
            IntelligenceInputIntegrityError,
        )
        normalization_size, normalization_sha = self._inspect_file(
            normalization_path,
            IntelligenceInputIntegrityError,
        )
        normalized_size, normalized_sha = self._inspect_file(
            normalized_audio_path,
            IntelligenceInputIntegrityError,
        )

        context = _IntelligenceContext(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            manifest=manifest,
            normalization=normalization,
            transcription_metadata=transcription_metadata,
            cleanup_metadata=cleanup_metadata,
            raw_transcript=raw_transcript,
            cleaned_transcript=cleaned_transcript,
            raw_json_path=raw_json_path,
            raw_json_size_bytes=raw_json_size,
            raw_json_sha256=raw_json_sha,
            raw_text_path=raw_text_path,
            raw_text_size_bytes=raw_text_size,
            raw_text_sha256=raw_text_sha,
            cleaned_json_path=cleaned_json_path,
            cleaned_json_size_bytes=cleaned_json_size,
            cleaned_json_sha256=cleaned_json_sha,
            cleaned_text_path=cleaned_text_path,
            cleaned_text_size_bytes=cleaned_text_size,
            cleaned_text_sha256=cleaned_text_sha,
            cleanup_metadata_path=cleanup_metadata_path,
            cleanup_metadata_size_bytes=cleanup_metadata_size,
            cleanup_metadata_sha256=cleanup_metadata_sha,
            transcription_metadata_path=transcription_path,
            transcription_metadata_size_bytes=transcription_size,
            transcription_metadata_sha256=transcription_sha,
            normalization_metadata_path=normalization_path,
            normalization_metadata_size_bytes=normalization_size,
            normalization_metadata_sha256=normalization_sha,
            normalized_audio_path=normalized_audio_path,
            normalized_audio_size_bytes=normalized_size,
            normalized_audio_sha256=normalized_sha,
        )
        self._validate_input_chain(context)
        return context

    def _load_model(
        self,
        path: Path,
        model_type: Any,
        missing_error: type[DecisionIntelligenceError],
        missing_message: str,
        invalid_message: str,
    ) -> Any:
        if not path.exists():
            raise missing_error(missing_message)
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise IntelligenceInputIntegrityError(invalid_message) from exc

    def _validate_input_chain(self, context: _IntelligenceContext) -> None:
        self._validate_normalization_contract(context)
        self._validate_transcription_contract(context)
        self._validate_raw_transcript_integrity(context)
        self._validate_cleanup_contract(context)
        self._validate_cleaned_transcript_integrity(context)
        self._validate_cleaned_against_raw(context)

    def _validate_normalization_contract(self, context: _IntelligenceContext) -> None:
        normalization = context.normalization
        if normalization.profile != CANONICAL_NORMALIZATION_PROFILE:
            raise IntelligenceInputIntegrityError("Normalization profile is invalid.")
        if normalization.input.relative_path != context.manifest.source.relative_path:
            raise IntelligenceInputIntegrityError("Normalization input path is invalid.")
        if normalization.input.size_bytes != context.manifest.source.size_bytes:
            raise IntelligenceInputIntegrityError("Normalization input size is invalid.")
        if normalization.input.sha256 != context.manifest.source.sha256:
            raise IntelligenceInputIntegrityError("Normalization input checksum is invalid.")
        if normalization.output.relative_path != NORMALIZED_AUDIO_RELATIVE_PATH:
            raise IntelligenceInputIntegrityError("Normalized audio path is invalid.")
        if normalization.output.size_bytes != context.normalized_audio_size_bytes:
            raise IntelligenceInputIntegrityError("Normalized audio size is invalid.")
        if normalization.output.sha256 != context.normalized_audio_sha256:
            raise IntelligenceInputIntegrityError("Normalized audio checksum is invalid.")

    def _validate_transcription_contract(self, context: _IntelligenceContext) -> None:
        metadata = context.transcription_metadata
        if metadata.provider.name != TRANSCRIPTION_PROVIDER_NAME:
            raise IntelligenceInputIntegrityError("Transcription provider is invalid.")
        if metadata.provider.model != TRANSCRIPTION_MODEL:
            raise IntelligenceInputIntegrityError("Transcription model is invalid.")
        if metadata.provider.response_format != TRANSCRIPTION_RESPONSE_FORMAT:
            raise IntelligenceInputIntegrityError("Transcription response format is invalid.")
        if metadata.provider.chunking_strategy != TRANSCRIPTION_CHUNKING_STRATEGY:
            raise IntelligenceInputIntegrityError("Transcription chunking strategy is invalid.")
        if metadata.input.relative_path != NORMALIZED_AUDIO_RELATIVE_PATH:
            raise IntelligenceInputIntegrityError("Transcription input path is invalid.")
        if metadata.input.size_bytes != context.normalized_audio_size_bytes:
            raise IntelligenceInputIntegrityError("Transcription input size is invalid.")
        if metadata.input.sha256 != context.normalized_audio_sha256:
            raise IntelligenceInputIntegrityError("Transcription input checksum is invalid.")
        if metadata.input.duration_seconds != context.normalization.output.duration_seconds:
            raise IntelligenceInputIntegrityError("Transcription input duration is invalid.")
        if metadata.transcript.structured_relative_path != TRANSCRIPT_JSON_RELATIVE_PATH:
            raise IntelligenceInputIntegrityError("Raw transcript JSON path is invalid.")
        if metadata.transcript.text_relative_path != TRANSCRIPT_TEXT_RELATIVE_PATH:
            raise IntelligenceInputIntegrityError("Raw transcript text path is invalid.")

    def _validate_raw_transcript_integrity(self, context: _IntelligenceContext) -> None:
        metadata = context.transcription_metadata.transcript
        if metadata.structured_size_bytes != context.raw_json_size_bytes:
            raise IntelligenceInputIntegrityError("Raw JSON size does not match metadata.")
        if metadata.structured_sha256 != context.raw_json_sha256:
            raise IntelligenceInputIntegrityError("Raw JSON checksum does not match metadata.")
        if metadata.text_size_bytes != context.raw_text_size_bytes:
            raise IntelligenceInputIntegrityError("Raw text size does not match metadata.")
        if metadata.text_sha256 != context.raw_text_sha256:
            raise IntelligenceInputIntegrityError("Raw text checksum does not match metadata.")
        if metadata.segment_count != len(context.raw_transcript.segments):
            raise IntelligenceInputIntegrityError("Raw segment count does not match metadata.")
        if metadata.speaker_labels != context.raw_transcript.speaker_labels:
            raise IntelligenceInputIntegrityError("Raw speaker labels do not match metadata.")
        if context.raw_text_path.read_text(encoding="utf-8") != render_raw_text(
            context.raw_transcript
        ):
            raise IntelligenceInputIntegrityError("Raw text is not deterministic.")

    def _validate_cleanup_contract(self, context: _IntelligenceContext) -> None:
        metadata = context.cleanup_metadata
        if metadata.prompt_version != CLEANUP_PROMPT_VERSION:
            raise IntelligenceInputIntegrityError("Cleanup prompt version is invalid.")
        if metadata.provider.name != "openai":
            raise IntelligenceInputIntegrityError("Cleanup provider is invalid.")
        if metadata.provider.model != CLEANUP_MODEL:
            raise IntelligenceInputIntegrityError("Cleanup model is invalid.")
        if metadata.provider.response_format_name != CLEANUP_RESPONSE_FORMAT_NAME:
            raise IntelligenceInputIntegrityError("Cleanup response schema is invalid.")
        if metadata.provider.store is not False:
            raise IntelligenceInputIntegrityError("Cleanup store flag is invalid.")
        if metadata.provider.strict_schema is not True:
            raise IntelligenceInputIntegrityError("Cleanup strict schema flag is invalid.")
        if metadata.input.raw_structured_sha256 != context.raw_json_sha256:
            raise IntelligenceInputIntegrityError("Cleanup raw JSON source is invalid.")
        if metadata.input.raw_text_sha256 != context.raw_text_sha256:
            raise IntelligenceInputIntegrityError("Cleanup raw text source is invalid.")
        if metadata.input.transcription_metadata_sha256 != context.transcription_metadata_sha256:
            raise IntelligenceInputIntegrityError("Cleanup transcription metadata source is invalid.")
        if metadata.input.normalization_metadata_sha256 != context.normalization_metadata_sha256:
            raise IntelligenceInputIntegrityError("Cleanup normalization metadata source is invalid.")
        if metadata.input.normalized_audio_sha256 != context.normalized_audio_sha256:
            raise IntelligenceInputIntegrityError("Cleanup normalized audio source is invalid.")
        if metadata.artifacts.structured_relative_path != CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH:
            raise IntelligenceInputIntegrityError("Cleaned transcript JSON path is invalid.")
        if metadata.artifacts.text_relative_path != CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH:
            raise IntelligenceInputIntegrityError("Cleaned transcript text path is invalid.")

    def _validate_cleaned_transcript_integrity(self, context: _IntelligenceContext) -> None:
        metadata = context.cleanup_metadata.artifacts
        cleaned = context.cleaned_transcript
        if metadata.structured_size_bytes != context.cleaned_json_size_bytes:
            raise IntelligenceInputIntegrityError("Cleaned JSON size does not match metadata.")
        if metadata.structured_sha256 != context.cleaned_json_sha256:
            raise IntelligenceInputIntegrityError("Cleaned JSON checksum does not match metadata.")
        if metadata.text_size_bytes != context.cleaned_text_size_bytes:
            raise IntelligenceInputIntegrityError("Cleaned text size does not match metadata.")
        if metadata.text_sha256 != context.cleaned_text_sha256:
            raise IntelligenceInputIntegrityError("Cleaned text checksum does not match metadata.")
        if metadata.segment_count != len(cleaned.segments):
            raise IntelligenceInputIntegrityError("Cleaned segment count does not match metadata.")
        if metadata.changed_segment_count != cleaned.changed_segment_count:
            raise IntelligenceInputIntegrityError("Cleaned changed count does not match metadata.")
        if metadata.unchanged_segment_count != cleaned.unchanged_segment_count:
            raise IntelligenceInputIntegrityError("Cleaned unchanged count does not match metadata.")
        if context.cleanup_metadata.input.raw_speaker_labels != context.raw_transcript.speaker_labels:
            raise IntelligenceInputIntegrityError("Cleanup speaker labels are invalid.")
        if cleaned.source_raw_transcript_sha256 != context.raw_json_sha256:
            raise IntelligenceInputIntegrityError("Cleaned source raw checksum is invalid.")
        if cleaned.prompt_version != CLEANUP_PROMPT_VERSION:
            raise IntelligenceInputIntegrityError("Cleaned prompt version is invalid.")
        if context.cleaned_text_path.read_text(encoding="utf-8") != render_cleaned_text(
            cleaned
        ):
            raise IntelligenceInputIntegrityError("Cleaned text is not deterministic.")
        if cleaned.text != self._combined_cleaned_text(cleaned.segments):
            raise IntelligenceInputIntegrityError("Cleaned combined text is invalid.")

    def _validate_cleaned_against_raw(self, context: _IntelligenceContext) -> None:
        raw = context.raw_transcript
        cleaned = context.cleaned_transcript
        if len(raw.segments) != len(cleaned.segments):
            raise IntelligenceInputIntegrityError("Cleaned/raw segment count mismatch.")
        if cleaned.speaker_labels != raw.speaker_labels:
            raise IntelligenceInputIntegrityError("Cleaned speaker labels mismatch.")
        for raw_segment, cleaned_segment in zip(raw.segments, cleaned.segments, strict=True):
            if cleaned_segment.segment_id != raw_segment.segment_id:
                raise IntelligenceInputIntegrityError("Cleaned segment ID mismatch.")
            if cleaned_segment.start_seconds != raw_segment.start_seconds:
                raise IntelligenceInputIntegrityError("Cleaned start timestamp mismatch.")
            if cleaned_segment.end_seconds != raw_segment.end_seconds:
                raise IntelligenceInputIntegrityError("Cleaned end timestamp mismatch.")
            if cleaned_segment.speaker_label != raw_segment.speaker_label:
                raise IntelligenceInputIntegrityError("Cleaned speaker mismatch.")
            if cleaned_segment.raw_text_sha256 != self._hash_text(raw_segment.text):
                raise IntelligenceInputIntegrityError("Cleaned raw segment hash mismatch.")
            if cleaned_segment.changed != (cleaned_segment.cleaned_text != raw_segment.text):
                raise IntelligenceInputIntegrityError("Cleaned changed flag mismatch.")
            if self._protected_tokens(raw_segment.text) != self._protected_tokens(
                cleaned_segment.cleaned_text
            ):
                raise IntelligenceInputIntegrityError("Phase 5 protected-token violation.")

    def _serialize_transcript_payload(self, context: _IntelligenceContext) -> str:
        payload = {
            "meeting_id": context.meeting_id,
            "segments": [
                {
                    "cleaned_text": segment.cleaned_text,
                    "end_seconds": segment.end_seconds,
                    "segment_id": segment.segment_id,
                    "segment_order": index,
                    "speaker_label": segment.speaker_label,
                    "start_seconds": segment.start_seconds,
                }
                for index, segment in enumerate(context.cleaned_transcript.segments, start=1)
            ],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _validate_provider_intelligence(
        self,
        context: _IntelligenceContext,
        provider: ProviderDecisionIntelligence,
    ) -> None:
        category_lists = self._provider_category_lists(provider)
        for category, items in category_lists.items():
            if len(items) > self._settings.intelligence_max_items_per_category:
                raise IntelligenceProviderResponseError(
                    f"{category} exceeds the configured item limit."
                )
        self._validate_summary(context, provider)
        self._validate_provider_evidence_and_fields(context, provider)
        for category, items in category_lists.items():
            self._reject_duplicates(category, items)
        logger.info("Decision intelligence evidence validation completed")
        logger.info("Decision intelligence actor validation completed")
        logger.info("Decision intelligence deadline validation completed")

    def _validate_summary(
        self,
        context: _IntelligenceContext,
        provider: ProviderDecisionIntelligence,
    ) -> None:
        summary = provider.executive_summary
        self._validate_text(summary.overview, "executive_summary.overview", allow_empty=True)
        if summary.overview.strip():
            self._validate_evidence_ids(context, summary.evidence_segment_ids)
        elif summary.evidence_segment_ids:
            self._validate_evidence_ids(context, summary.evidence_segment_ids)
        for outcome in summary.key_outcomes:
            self._validate_text(outcome.statement, "executive_summary.key_outcomes.statement")
            self._validate_evidence_ids(context, outcome.evidence_segment_ids)

    def _validate_provider_evidence_and_fields(
        self,
        context: _IntelligenceContext,
        provider: ProviderDecisionIntelligence,
    ) -> None:
        for item in provider.discussion_areas:
            self._validate_text(item.title, "discussion.title")
            self._validate_text(item.summary, "discussion.summary")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
        for item in provider.decisions:
            self._validate_text(item.statement, "decision.statement")
            self._validate_optional_text(item.rationale, "decision.rationale")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
        for item in provider.action_items:
            evidence_text = self._evidence_text(context, item.evidence_segment_ids)
            self._validate_text(item.description, "action.description")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
            self._validate_actor(context, item.owner, evidence_text)
            self._validate_deadline(item.deadline, evidence_text)
        for item in provider.commitments:
            evidence_text = self._evidence_text(context, item.evidence_segment_ids)
            self._validate_text(item.statement, "commitment.statement")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
            self._validate_actor(context, item.actor, evidence_text)
            self._validate_deadline(item.deadline, evidence_text)
        for item in provider.follow_ups:
            evidence_text = self._evidence_text(context, item.evidence_segment_ids)
            self._validate_text(item.description, "follow_up.description")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
            self._validate_actor(context, item.owner, evidence_text)
            self._validate_deadline(item.deadline, evidence_text)
        for item in provider.stakeholders:
            evidence_text = self._evidence_text(context, item.evidence_segment_ids)
            self._validate_text(item.position, "stakeholder.position")
            for concern in item.concerns:
                self._validate_text(concern, "stakeholder.concern")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
            self._validate_actor(context, item.actor, evidence_text)
        for item in provider.risks:
            self._validate_text(item.description, "risk.description")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
        for item in provider.blockers:
            evidence_text = self._evidence_text(context, item.evidence_segment_ids)
            self._validate_text(item.description, "blocker.description")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
            self._validate_actor(context, item.responsible_actor, evidence_text)
        for item in provider.dependencies:
            evidence_text = self._evidence_text(context, item.evidence_segment_ids)
            self._validate_text(item.description, "dependency.description")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
            self._validate_actor(context, item.dependency_on, evidence_text)
        for item in provider.opportunities:
            self._validate_text(item.description, "opportunity.description")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
        for item in provider.unresolved_questions:
            evidence_text = self._evidence_text(context, item.evidence_segment_ids)
            self._validate_text(item.question, "question.question")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
            self._validate_actor(context, item.asked_by, evidence_text)
        for item in provider.missing_information:
            self._validate_text(item.description, "missing_information.description")
            self._validate_optional_text(item.required_for, "missing_information.required_for")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
        for item in provider.strategic_insights:
            self._validate_text(item.insight, "insight.insight")
            self._validate_evidence_ids(context, item.evidence_segment_ids)
        for item in provider.recommendations:
            self._validate_text(item.recommendation, "recommendation.recommendation")
            self._validate_text(item.rationale, "recommendation.rationale")
            self._validate_evidence_ids(context, item.evidence_segment_ids)

    def _build_canonical_intelligence(
        self,
        context: _IntelligenceContext,
        provider: ProviderDecisionIntelligence,
    ) -> DecisionIntelligenceArtifact:
        summary = ExecutiveSummary(
            overview=provider.executive_summary.overview.strip(),
            evidence=self._resolve_evidence(context, provider.executive_summary.evidence_segment_ids),
            key_outcomes=[
                KeyOutcome(
                    outcome_id=self._id("outcome", index),
                    statement=item.statement.strip(),
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.executive_summary.key_outcomes, start=1)
            ],
        )
        action_items = [
            ActionItem(
                action_id=self._id("action", index),
                description=item.description.strip(),
                owner=IntelligenceActor.model_validate(item.owner.model_dump()),
                deadline=IntelligenceDeadline.model_validate(item.deadline.model_dump()),
                priority=item.priority,
                priority_basis=item.priority_basis,
                status=item.status,
                evidence=self._resolve_evidence(context, item.evidence_segment_ids),
            )
            for index, item in enumerate(provider.action_items, start=1)
        ]
        commitments = [
            Commitment(
                commitment_id=self._id("commitment", index),
                statement=item.statement.strip(),
                actor=IntelligenceActor.model_validate(item.actor.model_dump()),
                deadline=IntelligenceDeadline.model_validate(item.deadline.model_dump()),
                evidence=self._resolve_evidence(context, item.evidence_segment_ids),
            )
            for index, item in enumerate(provider.commitments, start=1)
        ]
        follow_ups = [
            FollowUp(
                follow_up_id=self._id("follow_up", index),
                description=item.description.strip(),
                owner=IntelligenceActor.model_validate(item.owner.model_dump()),
                deadline=IntelligenceDeadline.model_validate(item.deadline.model_dump()),
                evidence=self._resolve_evidence(context, item.evidence_segment_ids),
            )
            for index, item in enumerate(provider.follow_ups, start=1)
        ]
        missing_information = [
            MissingInformation(
                missing_info_id=self._id("missing_info", index),
                description=item.description.strip(),
                required_for=self._trim_optional(item.required_for),
                evidence=self._resolve_evidence(context, item.evidence_segment_ids),
            )
            for index, item in enumerate(provider.missing_information, start=1)
        ]

        artifact = DecisionIntelligenceArtifact(
            meeting_id=context.meeting_id,
            source_cleaned_transcript_sha256=context.cleaned_json_sha256,
            source_cleanup_metadata_sha256=context.cleanup_metadata_sha256,
            executive_summary=summary,
            discussion_areas=[
                DiscussionArea(
                    discussion_id=self._id("discussion", index),
                    title=item.title.strip(),
                    summary=item.summary.strip(),
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.discussion_areas, start=1)
            ],
            decisions=[
                Decision(
                    decision_id=self._id("decision", index),
                    statement=item.statement.strip(),
                    status=item.status,
                    rationale=self._trim_optional(item.rationale),
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.decisions, start=1)
            ],
            action_items=action_items,
            commitments=commitments,
            follow_ups=follow_ups,
            stakeholders=[
                StakeholderPosition(
                    stakeholder_id=self._id("stakeholder", index),
                    actor=IntelligenceActor.model_validate(item.actor.model_dump()),
                    position=item.position.strip(),
                    stance=item.stance,
                    concerns=[concern.strip() for concern in item.concerns],
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.stakeholders, start=1)
            ],
            risks=[
                Risk(
                    risk_id=self._id("risk", index),
                    description=item.description.strip(),
                    severity=item.severity,
                    likelihood=item.likelihood,
                    basis=item.basis,
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.risks, start=1)
            ],
            blockers=[
                Blocker(
                    blocker_id=self._id("blocker", index),
                    description=item.description.strip(),
                    responsible_actor=IntelligenceActor.model_validate(
                        item.responsible_actor.model_dump()
                    ),
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.blockers, start=1)
            ],
            dependencies=[
                Dependency(
                    dependency_id=self._id("dependency", index),
                    description=item.description.strip(),
                    dependency_on=IntelligenceActor.model_validate(
                        item.dependency_on.model_dump()
                    ),
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.dependencies, start=1)
            ],
            opportunities=[
                Opportunity(
                    opportunity_id=self._id("opportunity", index),
                    description=item.description.strip(),
                    basis=item.basis,
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.opportunities, start=1)
            ],
            unresolved_questions=[
                UnresolvedQuestion(
                    question_id=self._id("question", index),
                    question=item.question.strip(),
                    asked_by=IntelligenceActor.model_validate(item.asked_by.model_dump()),
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.unresolved_questions, start=1)
            ],
            missing_information=missing_information,
            strategic_insights=[
                StrategicInsight(
                    insight_id=self._id("insight", index),
                    insight=item.insight.strip(),
                    confidence=item.confidence,
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.strategic_insights, start=1)
            ],
            recommendations=[
                Recommendation(
                    recommendation_id=self._id("recommendation", index),
                    recommendation=item.recommendation.strip(),
                    priority=item.priority,
                    rationale=item.rationale.strip(),
                    evidence=self._resolve_evidence(context, item.evidence_segment_ids),
                )
                for index, item in enumerate(provider.recommendations, start=1)
            ],
            gaps=[],
        )
        artifact.gaps = self._derive_gaps(artifact)
        return artifact

    def _derive_gaps(self, artifact: DecisionIntelligenceArtifact) -> list[IntelligenceGap]:
        gaps: list[IntelligenceGap] = []

        def append_gap(
            kind: str,
            description: str,
            related_item_type: str,
            related_item_id: str,
            evidence: list[IntelligenceEvidenceReference],
        ) -> None:
            gaps.append(
                IntelligenceGap(
                    gap_id=self._id("gap", len(gaps) + 1),
                    kind=kind,
                    description=description,
                    related_item_type=related_item_type,
                    related_item_id=related_item_id,
                    evidence=evidence,
                )
            )

        for item in artifact.action_items:
            if item.owner.kind == "unknown":
                append_gap(
                    "missing_owner",
                    f"Owner is missing for action {item.action_id}.",
                    "action_item",
                    item.action_id,
                    item.evidence,
                )
            self._append_deadline_gap(
                append_gap,
                item.deadline,
                "action_item",
                item.action_id,
                item.evidence,
            )
        for item in artifact.commitments:
            self._append_deadline_gap(
                append_gap,
                item.deadline,
                "commitment",
                item.commitment_id,
                item.evidence,
            )
        for item in artifact.follow_ups:
            if item.owner.kind == "unknown":
                append_gap(
                    "missing_owner",
                    f"Owner is missing for follow-up {item.follow_up_id}.",
                    "follow_up",
                    item.follow_up_id,
                    item.evidence,
                )
            self._append_deadline_gap(
                append_gap,
                item.deadline,
                "follow_up",
                item.follow_up_id,
                item.evidence,
            )
        for item in artifact.missing_information:
            append_gap(
                "missing_information",
                item.description,
                "missing_information",
                item.missing_info_id,
                item.evidence,
            )
        return gaps

    def _append_deadline_gap(
        self,
        append_gap: Callable[[str, str, str, str, list[IntelligenceEvidenceReference]], None],
        deadline: IntelligenceDeadline,
        related_item_type: str,
        related_item_id: str,
        evidence: list[IntelligenceEvidenceReference],
    ) -> None:
        if deadline.status == "missing":
            append_gap(
                "missing_deadline",
                f"Deadline is missing for {related_item_type} {related_item_id}.",
                related_item_type,
                related_item_id,
                evidence,
            )
        elif deadline.status == "ambiguous":
            append_gap(
                "ambiguous_deadline",
                f"Deadline is ambiguous for {related_item_type} {related_item_id}.",
                related_item_type,
                related_item_id,
                evidence,
            )

    def _validate_canonical_intelligence(
        self,
        context: _IntelligenceContext,
        artifact: DecisionIntelligenceArtifact,
        *,
        error_type: type[DecisionIntelligenceError] = IntelligenceStateError,
    ) -> None:
        if artifact.meeting_id != context.meeting_id:
            raise error_type("Intelligence meeting ID does not match.")
        if artifact.source_cleaned_transcript_sha256 != context.cleaned_json_sha256:
            raise error_type("Intelligence cleaned transcript source does not match.")
        if artifact.source_cleanup_metadata_sha256 != context.cleanup_metadata_sha256:
            raise error_type("Intelligence cleanup metadata source does not match.")
        if artifact.prompt_version != INTELLIGENCE_PROMPT_VERSION:
            raise error_type("Intelligence prompt version does not match.")
        self._validate_id_sequences(artifact, error_type)
        self._validate_canonical_evidence_actors_deadlines(context, artifact, error_type)
        expected_gaps = self._derive_gaps(artifact.model_copy(update={"gaps": []}))
        if [gap.model_dump() for gap in artifact.gaps] != [
            gap.model_dump() for gap in expected_gaps
        ]:
            raise error_type("Intelligence gaps are not locally derived.")
        counts = artifact.category_counts()
        for category, value in counts.model_dump().items():
            if value > self._settings.intelligence_max_items_per_category:
                raise error_type(f"{category} exceeds the configured item limit.")

    def _validate_id_sequences(
        self,
        artifact: DecisionIntelligenceArtifact,
        error_type: type[DecisionIntelligenceError],
    ) -> None:
        sequences = {
            "outcome": [item.outcome_id for item in artifact.executive_summary.key_outcomes],
            "discussion": [item.discussion_id for item in artifact.discussion_areas],
            "decision": [item.decision_id for item in artifact.decisions],
            "action": [item.action_id for item in artifact.action_items],
            "commitment": [item.commitment_id for item in artifact.commitments],
            "follow_up": [item.follow_up_id for item in artifact.follow_ups],
            "stakeholder": [item.stakeholder_id for item in artifact.stakeholders],
            "risk": [item.risk_id for item in artifact.risks],
            "blocker": [item.blocker_id for item in artifact.blockers],
            "dependency": [item.dependency_id for item in artifact.dependencies],
            "opportunity": [item.opportunity_id for item in artifact.opportunities],
            "question": [item.question_id for item in artifact.unresolved_questions],
            "missing_info": [item.missing_info_id for item in artifact.missing_information],
            "insight": [item.insight_id for item in artifact.strategic_insights],
            "recommendation": [item.recommendation_id for item in artifact.recommendations],
            "gap": [item.gap_id for item in artifact.gaps],
        }
        all_ids: list[str] = []
        for prefix, ids in sequences.items():
            expected = [self._id(prefix, index) for index in range(1, len(ids) + 1)]
            if ids != expected:
                raise error_type(f"{prefix} IDs are not sequential.")
            all_ids.extend(ids)
        if len(set(all_ids)) != len(all_ids):
            raise error_type("Canonical IDs are not unique.")

    def _validate_canonical_evidence_actors_deadlines(
        self,
        context: _IntelligenceContext,
        artifact: DecisionIntelligenceArtifact,
        error_type: type[DecisionIntelligenceError],
    ) -> None:
        for evidence in self._canonical_evidence_lists(artifact):
            self._validate_evidence_references(context, evidence, error_type)
        for actor, evidence in self._canonical_actors(artifact):
            self._validate_canonical_actor(context, actor, evidence, error_type)
        for deadline, evidence in self._canonical_deadlines(artifact):
            self._validate_canonical_deadline(deadline, self._reference_text(context, evidence), error_type)

    def _build_metadata(
        self,
        context: _IntelligenceContext,
        intelligence: DecisionIntelligenceArtifact,
        intelligence_size: int,
        intelligence_sha: str,
        *,
        provider_request_count: int,
        input_character_count: int,
        usage: IntelligenceUsage | None,
    ) -> DecisionIntelligenceMetadata:
        return DecisionIntelligenceMetadata(
            meeting_id=context.meeting_id,
            created_at_utc=self._utc_now(),
            provider=IntelligenceProviderMetadata(
                model=self._settings.intelligence_model,
            ),
            input=IntelligenceInputMetadata(
                cleaned_json_size_bytes=context.cleaned_json_size_bytes,
                cleaned_json_sha256=context.cleaned_json_sha256,
                cleaned_text_size_bytes=context.cleaned_text_size_bytes,
                cleaned_text_sha256=context.cleaned_text_sha256,
                cleanup_metadata_size_bytes=context.cleanup_metadata_size_bytes,
                cleanup_metadata_sha256=context.cleanup_metadata_sha256,
                segment_count=len(context.cleaned_transcript.segments),
                speaker_labels=context.cleaned_transcript.speaker_labels,
            ),
            output=IntelligenceOutputMetadata(
                intelligence_size_bytes=intelligence_size,
                intelligence_sha256=intelligence_sha,
                category_counts=intelligence.category_counts(),
            ),
            processing=IntelligenceProcessingMetadata(
                provider_request_count=provider_request_count,
                input_character_count=input_character_count,
                max_input_characters=self._settings.intelligence_max_input_characters,
                max_items_per_category=(
                    self._settings.intelligence_max_items_per_category
                ),
            ),
            usage=usage,
        )

    def _reuse_existing_if_valid(
        self,
        context: _IntelligenceContext,
        intelligence_path: Path,
        metadata_path: Path,
    ) -> DecisionIntelligenceResult | None:
        existing = [intelligence_path.exists(), metadata_path.exists()]
        if any(existing) and not all(existing):
            raise IntelligenceStateError(
                "Meeting package contains an inconsistent intelligence state."
            )
        if not any(existing):
            return None

        try:
            intelligence = DecisionIntelligenceArtifact.model_validate_json(
                intelligence_path.read_text(encoding="utf-8")
            )
            metadata = DecisionIntelligenceMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
            self._validate_existing_metadata(
                context,
                intelligence,
                metadata,
                intelligence_path,
            )
        except (OSError, ValidationError, ValueError, DecisionIntelligenceError) as exc:
            if isinstance(exc, IntelligenceStateError):
                raise
            raise IntelligenceStateError(
                "Meeting package contains an inconsistent intelligence state."
            ) from exc

        logger.info("Decision intelligence reuse validated for meeting %s", context.meeting_id)
        return DecisionIntelligenceResult(
            meeting_id=context.meeting_id,
            meeting_dir=context.meeting_dir.resolve(strict=False),
            intelligence_json_path=intelligence_path.resolve(strict=False),
            intelligence_metadata_path=metadata_path.resolve(strict=False),
            intelligence=intelligence,
            metadata=metadata,
            reused_existing=True,
        )

    def _validate_existing_metadata(
        self,
        context: _IntelligenceContext,
        intelligence: DecisionIntelligenceArtifact,
        metadata: DecisionIntelligenceMetadata,
        intelligence_path: Path,
    ) -> None:
        if metadata.meeting_id != context.meeting_id:
            raise IntelligenceStateError("Intelligence metadata meeting ID does not match.")
        if metadata.provider.name != INTELLIGENCE_PROVIDER_NAME:
            raise IntelligenceStateError("Intelligence provider does not match.")
        if metadata.provider.model != self._settings.intelligence_model:
            raise IntelligenceStateError("Intelligence model does not match.")
        if metadata.provider.prompt_version != INTELLIGENCE_PROMPT_VERSION:
            raise IntelligenceStateError("Intelligence prompt version does not match.")
        if metadata.provider.response_schema != INTELLIGENCE_RESPONSE_SCHEMA_NAME:
            raise IntelligenceStateError("Intelligence response schema does not match.")
        if metadata.provider.store is not False:
            raise IntelligenceStateError("Intelligence store flag does not match.")
        if metadata.provider.reasoning_effort != INTELLIGENCE_REASONING_EFFORT:
            raise IntelligenceStateError("Intelligence reasoning effort does not match.")
        if metadata.input.cleaned_json_sha256 != context.cleaned_json_sha256:
            raise IntelligenceStateError("Cleaned JSON input checksum does not match.")
        if metadata.input.cleaned_text_sha256 != context.cleaned_text_sha256:
            raise IntelligenceStateError("Cleaned text input checksum does not match.")
        if metadata.input.cleanup_metadata_sha256 != context.cleanup_metadata_sha256:
            raise IntelligenceStateError("Cleanup metadata input checksum does not match.")
        if metadata.input.segment_count != len(context.cleaned_transcript.segments):
            raise IntelligenceStateError("Input segment count does not match.")
        if metadata.input.speaker_labels != context.cleaned_transcript.speaker_labels:
            raise IntelligenceStateError("Input speaker labels do not match.")
        intelligence_size, intelligence_sha = self._inspect_file(
            intelligence_path,
            IntelligenceStateError,
        )
        if metadata.output.intelligence_size_bytes != intelligence_size:
            raise IntelligenceStateError("Intelligence size does not match metadata.")
        if metadata.output.intelligence_sha256 != intelligence_sha:
            raise IntelligenceStateError("Intelligence checksum does not match metadata.")
        if metadata.output.category_counts != intelligence.category_counts():
            raise IntelligenceStateError("Intelligence category counts do not match.")
        self._validate_canonical_intelligence(context, intelligence)

    def _validate_evidence_ids(
        self,
        context: _IntelligenceContext,
        segment_ids: list[str],
    ) -> None:
        if not segment_ids:
            raise IntelligenceEvidenceError("Evidence segment IDs are required.")
        id_to_index = self._segment_index(context)
        seen: set[str] = set()
        previous_index = -1
        for segment_id in segment_ids:
            if segment_id not in id_to_index:
                raise IntelligenceEvidenceError("Evidence segment ID does not exist.")
            if segment_id in seen:
                raise IntelligenceEvidenceError("Evidence segment IDs must be unique.")
            index = id_to_index[segment_id]
            if index <= previous_index:
                raise IntelligenceEvidenceError("Evidence segment IDs are out of order.")
            seen.add(segment_id)
            previous_index = index

    def _resolve_evidence(
        self,
        context: _IntelligenceContext,
        segment_ids: list[str],
    ) -> list[IntelligenceEvidenceReference]:
        self._validate_evidence_ids(context, segment_ids)
        segment_by_id = {
            segment.segment_id: segment for segment in context.cleaned_transcript.segments
        }
        return [
            IntelligenceEvidenceReference(
                segment_id=segment.segment_id,
                speaker_label=segment.speaker_label,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                cleaned_text_sha256=self._hash_text(segment.cleaned_text),
            )
            for segment_id in segment_ids
            for segment in [segment_by_id[segment_id]]
        ]

    def _validate_evidence_references(
        self,
        context: _IntelligenceContext,
        evidence: list[IntelligenceEvidenceReference],
        error_type: type[DecisionIntelligenceError],
    ) -> None:
        if not evidence:
            raise error_type("Canonical evidence references are required.")
        segment_by_id = {
            segment.segment_id: segment for segment in context.cleaned_transcript.segments
        }
        self._validate_evidence_ids(context, [item.segment_id for item in evidence])
        for item in evidence:
            segment = segment_by_id[item.segment_id]
            if item.speaker_label != segment.speaker_label:
                raise error_type("Evidence speaker label does not match.")
            if item.start_seconds != segment.start_seconds:
                raise error_type("Evidence start timestamp does not match.")
            if item.end_seconds != segment.end_seconds:
                raise error_type("Evidence end timestamp does not match.")
            if item.cleaned_text_sha256 != self._hash_text(segment.cleaned_text):
                raise error_type("Evidence text checksum does not match.")

    def _validate_actor(
        self,
        context: _IntelligenceContext,
        actor: Any,
        evidence_text: str,
    ) -> None:
        try:
            canonical = IntelligenceActor.model_validate(actor.model_dump())
        except (AttributeError, ValidationError, ValueError) as exc:
            raise IntelligenceActorError("Actor reference is invalid.") from exc
        self._validate_canonical_actor(
            context,
            canonical,
            evidence_text,
            IntelligenceActorError,
        )

    def _validate_canonical_actor(
        self,
        context: _IntelligenceContext,
        actor: IntelligenceActor,
        evidence: str | list[IntelligenceEvidenceReference],
        error_type: type[DecisionIntelligenceError],
    ) -> None:
        evidence_text = (
            self._reference_text(context, evidence)
            if isinstance(evidence, list)
            else evidence
        )
        if actor.kind == "speaker_label":
            if actor.value not in context.cleaned_transcript.speaker_labels:
                raise error_type("Speaker-label actor does not exist.")
            return
        if actor.kind == "unknown":
            if actor.value is not None:
                raise error_type("Unknown actor must use null value.")
            return
        if actor.value is None:
            raise error_type("Actor value is required.")
        if actor.value.casefold() not in evidence_text.casefold():
            raise error_type("Actor value is not present in evidence text.")

    def _validate_deadline(
        self,
        deadline: Any,
        evidence_text: str,
    ) -> None:
        try:
            canonical = IntelligenceDeadline.model_validate(deadline.model_dump())
        except (AttributeError, ValidationError, ValueError) as exc:
            raise IntelligenceDeadlineError("Deadline reference is invalid.") from exc
        self._validate_canonical_deadline(
            canonical,
            evidence_text,
            IntelligenceDeadlineError,
        )

    def _validate_canonical_deadline(
        self,
        deadline: IntelligenceDeadline,
        evidence_text: str,
        error_type: type[DecisionIntelligenceError],
    ) -> None:
        if deadline.status in {"explicit", "ambiguous"}:
            if deadline.text is None:
                raise error_type("Deadline text is required.")
            if deadline.text.casefold() not in evidence_text.casefold():
                raise error_type("Deadline text is not present in evidence.")
        elif deadline.text is not None:
            raise error_type("Deadline text must be null.")

    def _validate_text(
        self,
        value: str,
        field_name: str,
        *,
        allow_empty: bool = False,
    ) -> None:
        if CONTROL_CHARACTER_PATTERN.search(value):
            raise IntelligenceProviderResponseError(f"{field_name} contains control characters.")
        normalized = value.strip()
        if not allow_empty and not normalized:
            raise IntelligenceProviderResponseError(f"{field_name} must be nonempty.")

    def _validate_optional_text(self, value: str | None, field_name: str) -> None:
        if value is not None:
            self._validate_text(value, field_name)

    def _reject_duplicates(self, category: str, items: list[Any]) -> None:
        seen: set[str] = set()
        for item in items:
            payload = item.model_dump(mode="json", exclude={"evidence_segment_ids"})
            key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if key in seen:
                raise IntelligenceProviderResponseError(
                    f"{category} contains duplicate items."
                )
            seen.add(key)

    def _provider_category_lists(
        self,
        provider: ProviderDecisionIntelligence,
    ) -> dict[str, list[Any]]:
        return {
            "discussion_areas": provider.discussion_areas,
            "decisions": provider.decisions,
            "action_items": provider.action_items,
            "commitments": provider.commitments,
            "follow_ups": provider.follow_ups,
            "stakeholders": provider.stakeholders,
            "risks": provider.risks,
            "blockers": provider.blockers,
            "dependencies": provider.dependencies,
            "opportunities": provider.opportunities,
            "unresolved_questions": provider.unresolved_questions,
            "missing_information": provider.missing_information,
            "strategic_insights": provider.strategic_insights,
            "recommendations": provider.recommendations,
        }

    def _canonical_evidence_lists(
        self,
        artifact: DecisionIntelligenceArtifact,
    ) -> Iterable[list[IntelligenceEvidenceReference]]:
        if artifact.executive_summary.overview:
            yield artifact.executive_summary.evidence
        for item in artifact.executive_summary.key_outcomes:
            yield item.evidence
        for collection in [
            artifact.discussion_areas,
            artifact.decisions,
            artifact.action_items,
            artifact.commitments,
            artifact.follow_ups,
            artifact.stakeholders,
            artifact.risks,
            artifact.blockers,
            artifact.dependencies,
            artifact.opportunities,
            artifact.unresolved_questions,
            artifact.missing_information,
            artifact.strategic_insights,
            artifact.recommendations,
            artifact.gaps,
        ]:
            for item in collection:
                yield item.evidence

    def _canonical_actors(
        self,
        artifact: DecisionIntelligenceArtifact,
    ) -> Iterable[tuple[IntelligenceActor, list[IntelligenceEvidenceReference]]]:
        for item in artifact.action_items:
            yield item.owner, item.evidence
        for item in artifact.commitments:
            yield item.actor, item.evidence
        for item in artifact.follow_ups:
            yield item.owner, item.evidence
        for item in artifact.stakeholders:
            yield item.actor, item.evidence
        for item in artifact.blockers:
            yield item.responsible_actor, item.evidence
        for item in artifact.dependencies:
            yield item.dependency_on, item.evidence
        for item in artifact.unresolved_questions:
            yield item.asked_by, item.evidence

    def _canonical_deadlines(
        self,
        artifact: DecisionIntelligenceArtifact,
    ) -> Iterable[tuple[IntelligenceDeadline, list[IntelligenceEvidenceReference]]]:
        for item in artifact.action_items:
            yield item.deadline, item.evidence
        for item in artifact.commitments:
            yield item.deadline, item.evidence
        for item in artifact.follow_ups:
            yield item.deadline, item.evidence

    def _evidence_text(
        self,
        context: _IntelligenceContext,
        segment_ids: list[str],
    ) -> str:
        self._validate_evidence_ids(context, segment_ids)
        segment_by_id = {
            segment.segment_id: segment for segment in context.cleaned_transcript.segments
        }
        return "\n".join(segment_by_id[segment_id].cleaned_text for segment_id in segment_ids)

    def _reference_text(
        self,
        context: _IntelligenceContext,
        evidence: list[IntelligenceEvidenceReference],
    ) -> str:
        segment_by_id = {
            segment.segment_id: segment for segment in context.cleaned_transcript.segments
        }
        return "\n".join(segment_by_id[item.segment_id].cleaned_text for item in evidence)

    def _segment_index(self, context: _IntelligenceContext) -> dict[str, int]:
        return {
            segment.segment_id: index
            for index, segment in enumerate(context.cleaned_transcript.segments)
        }

    def _write_json_atomically(
        self,
        model: DecisionIntelligenceArtifact | DecisionIntelligenceMetadata,
        path: Path,
        error_type: type[DecisionIntelligenceError],
        message: str,
    ) -> None:
        payload = model.model_dump_json(indent=2) + "\n"
        self._write_text_atomically(payload, path, error_type, message)

    def _write_text_atomically(
        self,
        payload: str,
        path: Path,
        error_type: type[DecisionIntelligenceError],
        message: str,
    ) -> None:
        temp_path = path.with_name(f".tmp_{uuid.uuid4().hex[:12]}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove temporary intelligence artifact")
            raise error_type(message) from exc

    def _publish_artifacts(
        self,
        staged_intelligence_path: Path,
        intelligence_path: Path,
        staged_metadata_path: Path,
        metadata_path: Path,
    ) -> None:
        published: list[Path] = []
        try:
            intelligence_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if intelligence_path.exists() or metadata_path.exists():
                raise IntelligenceStateError(
                    "Meeting package contains an inconsistent intelligence state."
                )
            os.replace(staged_intelligence_path, intelligence_path)
            published.append(intelligence_path)
            if metadata_path.exists():
                raise IntelligenceStateError(
                    "Meeting package contains an inconsistent intelligence state."
                )
            os.replace(staged_metadata_path, metadata_path)
            published.append(metadata_path)
        except IntelligenceStateError:
            self._remove_published_artifacts(published)
            raise
        except OSError as exc:
            self._remove_published_artifacts(published)
            raise IntelligencePublicationError(
                "Decision intelligence artifacts could not be published."
            ) from exc

    def _remove_published_artifacts(self, paths: list[Path]) -> None:
        logger.info("Attempting decision intelligence artifact rollback")
        for path in reversed(paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Rollback failed for published intelligence artifact")

    def _rollback_staging(self, staging_dir: Path) -> None:
        try:
            if staging_dir.exists():
                logger.info("Rolling back staged intelligence artifacts")
                shutil.rmtree(staging_dir)
            staging_root = staging_dir.parent
            if staging_root.exists() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            logger.exception("Rollback failed for staged intelligence artifacts")

    def _resolve_package_relative_path(
        self,
        package_root: Path,
        relative_path: str,
        error_type: type[DecisionIntelligenceError],
    ) -> Path:
        self._assert_safe_package_relative_path(relative_path, error_type)
        pure_path = PurePosixPath(relative_path)
        resolved = package_root.joinpath(*pure_path.parts).resolve(strict=False)
        if not self._is_relative_to(resolved, package_root):
            raise error_type("Package-relative path escapes the meeting package.")
        return resolved

    def _assert_safe_package_relative_path(
        self,
        relative_path: str,
        error_type: type[DecisionIntelligenceError],
    ) -> None:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts or "\\" in relative_path:
            raise error_type("Package-relative path is unsafe.")

    def _inspect_file(
        self,
        path: Path,
        error_type: type[DecisionIntelligenceError],
    ) -> tuple[int, str]:
        try:
            if not path.exists() or not path.is_file():
                if error_type is IntelligenceInputIntegrityError:
                    raise IntelligenceInputNotFoundError(
                        "Decision intelligence input artifact is missing."
                    )
                raise error_type("Decision intelligence artifact is missing.")
            size_bytes = path.stat().st_size
        except OSError as exc:
            raise error_type("Decision intelligence artifact could not be inspected.") from exc

        if size_bytes <= 0:
            raise error_type("Decision intelligence artifact is empty.")
        return size_bytes, self._hash_file(path, error_type)

    def _hash_file(
        self,
        path: Path,
        error_type: type[DecisionIntelligenceError],
    ) -> str:
        checksum = hashlib.sha256()
        try:
            with path.open("rb") as file:
                while chunk := file.read(HASH_BUFFER_SIZE):
                    checksum.update(chunk)
        except OSError as exc:
            raise error_type("Artifact could not be hashed.") from exc
        return checksum.hexdigest()

    def _hash_text(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _combined_cleaned_text(
        self,
        segments: list[CleanedTranscriptSegment],
    ) -> str:
        return "\n".join(segment.cleaned_text for segment in segments)

    def _protected_tokens(self, value: str) -> Counter[str]:
        tokens: list[str] = []
        for match in PROTECTED_TOKEN_PATTERN.finditer(value):
            token = match.group(0).strip(TOKEN_EDGE_PUNCTUATION)
            if token:
                tokens.append(token)
        return Counter(tokens)

    def _trim_optional(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _id(self, prefix: str, index: int) -> str:
        return f"{prefix}_{index:03d}"

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _is_relative_to(self, child: Path, parent: Path) -> bool:
        try:
            child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except ValueError:
            return False
        return True
