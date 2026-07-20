"""Canonical transcript cleanup publication service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from backend.app.config import CLEANUP_MODEL, TRANSCRIPTION_MODEL, Settings, get_settings
from backend.app.models.cleanup import (
    CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH,
    CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH,
    CLEANUP_METADATA_RELATIVE_PATH,
    CLEANUP_PROMPT_VERSION,
    CLEANUP_PROVIDER_NAME,
    CLEANUP_RESPONSE_FORMAT_NAME,
    CleanedTranscript,
    CleanedTranscriptSegment,
    CleanupArtifactsMetadata,
    CleanupBatchingMetadata,
    CleanupInputMetadata,
    CleanupMetadata,
    CleanupProviderMetadata,
    CleanupUsage,
    TranscriptCleanupResult,
    render_cleaned_text,
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
    TranscriptSegment,
    TranscriptionMetadata,
    render_raw_text,
)
from backend.app.services.cleanup.errors import (
    CleanupFidelityError,
    CleanupInputIntegrityError,
    CleanupInputNotFoundError,
    CleanupMetadataWriteError,
    CleanupProviderResponseError,
    CleanupPublicationError,
    CleanupStateError,
    TranscriptCleanupError,
)
from backend.app.services.cleanup.openai_provider import OpenAICleanupProvider
from backend.app.services.cleanup.provider import (
    CleanupProvider,
    CleanupProviderRequest,
    CleanupProviderResult,
    CleanupProviderSegment,
    CleanupProviderSegmentResult,
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
MAX_EXPANSION_MULTIPLIER = 5
MAX_EXPANSION_EXTRA_CHARS = 200

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class _CleanupContext:
    meeting_id: str
    meeting_dir: Path
    manifest: MeetingManifest
    normalization: NormalizationMetadata
    transcription_metadata: TranscriptionMetadata
    raw_transcript: RawTranscript
    raw_json_path: Path
    raw_json_size_bytes: int
    raw_json_sha256: str
    raw_text_path: Path
    raw_text_size_bytes: int
    raw_text_sha256: str
    transcription_metadata_path: Path
    transcription_metadata_size_bytes: int
    transcription_metadata_sha256: str
    normalization_metadata_path: Path
    normalization_metadata_size_bytes: int
    normalization_metadata_sha256: str
    normalized_audio_path: Path
    normalized_audio_size_bytes: int
    normalized_audio_sha256: str


class TranscriptCleanupService:
    """Publish semantically faithful cleaned transcript artifacts."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: CleanupProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def clean_transcript(self, meeting_id: str) -> TranscriptCleanupResult:
        logger.info("Starting transcript cleanup for meeting %s", meeting_id)

        context = self._load_context(meeting_id)
        logger.info(
            "Raw transcript integrity verified for meeting %s with %s segments",
            meeting_id,
            len(context.raw_transcript.segments),
        )

        cleaned_json_path = context.meeting_dir / CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH
        cleaned_text_path = context.meeting_dir / CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH
        metadata_path = context.meeting_dir / CLEANUP_METADATA_RELATIVE_PATH

        existing_result = self._reuse_existing_if_valid(
            context,
            cleaned_json_path,
            cleaned_text_path,
            metadata_path,
        )
        if existing_result is not None:
            logger.info("Reusing existing transcript cleanup for meeting %s", meeting_id)
            return existing_result

        staging_dir = context.meeting_dir / ".staging" / f"cleanup_{uuid.uuid4().hex}"

        try:
            staging_transcript_dir = staging_dir / "transcript"
            staging_transcript_dir.mkdir(parents=True, exist_ok=False)
            staged_cleaned_json_path = staging_transcript_dir / "cleaned.json"
            staged_cleaned_text_path = staging_transcript_dir / "cleaned.txt"
            staged_metadata_path = staging_dir / "cleanup.json"

            batches = self._build_batches(context.raw_transcript.segments)
            logger.info(
                "Built %s transcript cleanup batches for meeting %s",
                len(batches),
                meeting_id,
            )
            provider_request_count = 0
            usage_values: list[CleanupUsage] = []
            cleaned_results: list[CleanupProviderSegmentResult] = []

            if batches:
                provider = self._provider or OpenAICleanupProvider(self._settings)
                for batch_index, batch in enumerate(batches, start=1):
                    request = self._provider_request(
                        context,
                        batch,
                        batch_index,
                        len(batches),
                    )
                    logger.info(
                        "Starting cleanup provider request %s/%s for meeting %s",
                        batch_index,
                        len(batches),
                        meeting_id,
                    )
                    provider_result = provider.clean_batch(request)
                    provider_request_count += 1
                    self._validate_provider_result_for_batch(
                        batch,
                        provider_result,
                    )
                    cleaned_results.extend(provider_result.segments)
                    if provider_result.usage is not None:
                        usage_values.append(provider_result.usage)
                    logger.info(
                        "Completed cleanup provider request %s/%s for meeting %s",
                        batch_index,
                        len(batches),
                        meeting_id,
                    )

            transcript = self._build_cleaned_transcript(
                context,
                cleaned_results,
            )
            self._validate_cleaned_transcript_against_raw(context.raw_transcript, transcript)
            logger.info(
                "Transcript cleanup fidelity validation completed for meeting %s",
                meeting_id,
            )

            self._write_json_atomically(
                transcript,
                staged_cleaned_json_path,
                CleanupPublicationError,
                "Cleaned transcript JSON could not be written.",
            )
            cleaned_text = render_cleaned_text(transcript)
            self._write_text_atomically(
                cleaned_text,
                staged_cleaned_text_path,
                CleanupPublicationError,
                "Cleaned transcript text could not be written.",
            )

            cleaned_json_size, cleaned_json_sha = self._inspect_file(
                staged_cleaned_json_path,
                CleanupPublicationError,
            )
            cleaned_text_size, cleaned_text_sha = self._inspect_file(
                staged_cleaned_text_path,
                CleanupPublicationError,
            )
            metadata = self._build_metadata(
                context,
                transcript,
                cleaned_json_size,
                cleaned_json_sha,
                cleaned_text_size,
                cleaned_text_sha,
                batch_count=len(batches),
                provider_request_count=provider_request_count,
                usage_values=usage_values,
            )
            self._write_json_atomically(
                metadata,
                staged_metadata_path,
                CleanupMetadataWriteError,
                "Cleanup metadata could not be written.",
            )
            self._publish_artifacts(
                staged_cleaned_json_path,
                cleaned_json_path,
                staged_cleaned_text_path,
                cleaned_text_path,
                staged_metadata_path,
                metadata_path,
            )

            logger.info(
                "Completed transcript cleanup for meeting %s with %s changed segments",
                meeting_id,
                transcript.changed_segment_count,
            )
            return TranscriptCleanupResult(
                meeting_id=context.meeting_id,
                meeting_dir=context.meeting_dir.resolve(strict=False),
                cleaned_json_path=cleaned_json_path.resolve(strict=False),
                cleaned_text_path=cleaned_text_path.resolve(strict=False),
                cleanup_metadata_path=metadata_path.resolve(strict=False),
                transcript=transcript,
                metadata=metadata,
                reused_existing=False,
            )
        except TranscriptCleanupError:
            logger.info("Transcript cleanup failed for meeting %s", meeting_id)
            raise
        except OSError as exc:
            logger.info("Transcript cleanup failed for meeting %s", meeting_id)
            raise CleanupPublicationError(
                "Cleanup artifacts could not be staged or published."
            ) from exc
        finally:
            self._rollback_staging(staging_dir)

    def _load_context(self, meeting_id: str) -> _CleanupContext:
        if not MEETING_ID_PATTERN.fullmatch(meeting_id):
            raise CleanupInputIntegrityError("Meeting ID is invalid.")

        meetings_dir = self._settings.meetings_dir
        meeting_dir = (meetings_dir / meeting_id).resolve(strict=False)
        if not self._is_relative_to(meeting_dir, meetings_dir):
            raise CleanupInputNotFoundError("Meeting package path is invalid.")
        if not meeting_dir.exists() or not meeting_dir.is_dir():
            raise CleanupInputNotFoundError("Meeting package was not found.")

        manifest = self._load_meeting_manifest(meeting_dir / "metadata" / "meeting.json")
        normalization_path = meeting_dir / "metadata" / "normalization.json"
        normalization = self._load_normalization_metadata(normalization_path)
        transcription_path = meeting_dir / TRANSCRIPTION_METADATA_RELATIVE_PATH
        transcription_metadata = self._load_transcription_metadata(transcription_path)
        raw_json_path = meeting_dir / TRANSCRIPT_JSON_RELATIVE_PATH
        raw_text_path = meeting_dir / TRANSCRIPT_TEXT_RELATIVE_PATH
        raw_transcript = self._load_raw_transcript(raw_json_path)

        if (
            manifest.meeting_id != meeting_id
            or normalization.meeting_id != meeting_id
            or transcription_metadata.meeting_id != meeting_id
            or raw_transcript.meeting_id != meeting_id
        ):
            raise CleanupInputIntegrityError(
                "Meeting metadata IDs do not match the requested meeting ID."
            )

        self._validate_phase4_contract(transcription_metadata)
        self._validate_normalization_contract(manifest, normalization)

        normalized_audio_path = self._resolve_package_relative_path(
            meeting_dir,
            normalization.output.relative_path,
            CleanupInputIntegrityError,
        )
        raw_json_size, raw_json_sha = self._inspect_file(
            raw_json_path,
            CleanupInputIntegrityError,
        )
        raw_text_size, raw_text_sha = self._inspect_file(
            raw_text_path,
            CleanupInputIntegrityError,
        )
        transcription_size, transcription_sha = self._inspect_file(
            transcription_path,
            CleanupInputIntegrityError,
        )
        normalization_size, normalization_sha = self._inspect_file(
            normalization_path,
            CleanupInputIntegrityError,
        )
        normalized_size, normalized_sha = self._inspect_file(
            normalized_audio_path,
            CleanupInputIntegrityError,
        )

        self._validate_raw_transcript_integrity(
            transcription_metadata,
            raw_transcript,
            raw_json_path,
            raw_text_path,
            raw_json_size,
            raw_json_sha,
            raw_text_size,
            raw_text_sha,
        )
        self._validate_normalized_audio_provenance(
            normalization,
            transcription_metadata,
            normalized_size,
            normalized_sha,
        )

        return _CleanupContext(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            manifest=manifest,
            normalization=normalization,
            transcription_metadata=transcription_metadata,
            raw_transcript=raw_transcript,
            raw_json_path=raw_json_path,
            raw_json_size_bytes=raw_json_size,
            raw_json_sha256=raw_json_sha,
            raw_text_path=raw_text_path,
            raw_text_size_bytes=raw_text_size,
            raw_text_sha256=raw_text_sha,
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

    def _load_meeting_manifest(self, manifest_path: Path) -> MeetingManifest:
        if not manifest_path.exists():
            raise CleanupInputNotFoundError("Meeting manifest was not found.")
        try:
            return MeetingManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise CleanupInputIntegrityError(
                "Meeting manifest is invalid for cleanup."
            ) from exc

    def _load_normalization_metadata(
        self,
        metadata_path: Path,
    ) -> NormalizationMetadata:
        if not metadata_path.exists():
            raise CleanupInputNotFoundError("Normalization metadata was not found.")
        try:
            return NormalizationMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise CleanupInputIntegrityError(
                "Normalization metadata is invalid for cleanup."
            ) from exc

    def _load_transcription_metadata(
        self,
        metadata_path: Path,
    ) -> TranscriptionMetadata:
        if not metadata_path.exists():
            raise CleanupInputNotFoundError("Transcription metadata was not found.")
        try:
            return TranscriptionMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise CleanupInputIntegrityError(
                "Transcription metadata is invalid for cleanup."
            ) from exc

    def _load_raw_transcript(self, raw_json_path: Path) -> RawTranscript:
        if not raw_json_path.exists():
            raise CleanupInputNotFoundError("Raw transcript JSON was not found.")
        try:
            return RawTranscript.model_validate_json(
                raw_json_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise CleanupInputIntegrityError("Raw transcript JSON is invalid.") from exc

    def _validate_phase4_contract(self, metadata: TranscriptionMetadata) -> None:
        if metadata.provider.name != TRANSCRIPTION_PROVIDER_NAME:
            raise CleanupInputIntegrityError("Transcription provider is not valid.")
        if metadata.provider.model != TRANSCRIPTION_MODEL:
            raise CleanupInputIntegrityError("Transcription model is not valid.")
        if metadata.provider.response_format != TRANSCRIPTION_RESPONSE_FORMAT:
            raise CleanupInputIntegrityError("Transcription response format is not valid.")
        if metadata.provider.chunking_strategy != TRANSCRIPTION_CHUNKING_STRATEGY:
            raise CleanupInputIntegrityError("Transcription chunking strategy is not valid.")
        if metadata.input.relative_path != NORMALIZED_AUDIO_RELATIVE_PATH:
            raise CleanupInputIntegrityError("Transcription input path is not canonical.")
        if metadata.input.normalization_profile != "convointel-stt-wav-v1":
            raise CleanupInputIntegrityError(
                "Transcription normalization profile is not valid."
            )
        if metadata.transcript.structured_relative_path != TRANSCRIPT_JSON_RELATIVE_PATH:
            raise CleanupInputIntegrityError("Raw JSON path is not canonical.")
        if metadata.transcript.text_relative_path != TRANSCRIPT_TEXT_RELATIVE_PATH:
            raise CleanupInputIntegrityError("Raw text path is not canonical.")

    def _validate_normalization_contract(
        self,
        manifest: MeetingManifest,
        normalization: NormalizationMetadata,
    ) -> None:
        if normalization.profile != CANONICAL_NORMALIZATION_PROFILE:
            raise CleanupInputIntegrityError(
                "Normalization profile does not match the canonical profile."
            )
        if normalization.input.sha256 != manifest.source.sha256:
            raise CleanupInputIntegrityError(
                "Normalization input checksum does not match the source manifest."
            )
        if normalization.input.size_bytes != manifest.source.size_bytes:
            raise CleanupInputIntegrityError(
                "Normalization input size does not match the source manifest."
            )
        if normalization.input.relative_path != manifest.source.relative_path:
            raise CleanupInputIntegrityError(
                "Normalization input path does not match the source manifest."
            )

    def _validate_raw_transcript_integrity(
        self,
        metadata: TranscriptionMetadata,
        transcript: RawTranscript,
        raw_json_path: Path,
        raw_text_path: Path,
        raw_json_size: int,
        raw_json_sha: str,
        raw_text_size: int,
        raw_text_sha: str,
    ) -> None:
        if metadata.transcript.structured_size_bytes != raw_json_size:
            raise CleanupInputIntegrityError("Raw JSON size does not match metadata.")
        if metadata.transcript.structured_sha256 != raw_json_sha:
            raise CleanupInputIntegrityError("Raw JSON checksum does not match metadata.")
        if metadata.transcript.text_size_bytes != raw_text_size:
            raise CleanupInputIntegrityError("Raw text size does not match metadata.")
        if metadata.transcript.text_sha256 != raw_text_sha:
            raise CleanupInputIntegrityError("Raw text checksum does not match metadata.")
        if metadata.transcript.segment_count != len(transcript.segments):
            raise CleanupInputIntegrityError(
                "Raw segment count does not match metadata."
            )
        if metadata.transcript.speaker_labels != transcript.speaker_labels:
            raise CleanupInputIntegrityError(
                "Raw speaker labels do not match metadata."
            )
        try:
            raw_text = raw_text_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CleanupInputIntegrityError("Raw text could not be read.") from exc
        if raw_text != render_raw_text(transcript):
            raise CleanupInputIntegrityError(
                "Raw text is not the deterministic rendering."
            )
        self._assert_safe_package_relative_path(
            str(raw_json_path.relative_to(raw_json_path.parents[1]).as_posix()),
            CleanupInputIntegrityError,
        )

    def _validate_normalized_audio_provenance(
        self,
        normalization: NormalizationMetadata,
        transcription: TranscriptionMetadata,
        normalized_size: int,
        normalized_sha: str,
    ) -> None:
        if normalization.output.size_bytes != normalized_size:
            raise CleanupInputIntegrityError(
                "Normalized audio size does not match normalization metadata."
            )
        if normalization.output.sha256 != normalized_sha:
            raise CleanupInputIntegrityError(
                "Normalized audio checksum does not match normalization metadata."
            )
        if transcription.input.size_bytes != normalized_size:
            raise CleanupInputIntegrityError(
                "Transcription input size does not match normalized audio."
            )
        if transcription.input.sha256 != normalized_sha:
            raise CleanupInputIntegrityError(
                "Transcription input checksum does not match normalized audio."
            )
        if (
            transcription.input.duration_seconds
            != normalization.output.duration_seconds
        ):
            raise CleanupInputIntegrityError(
                "Transcription input duration does not match normalization metadata."
            )

    def _reuse_existing_if_valid(
        self,
        context: _CleanupContext,
        cleaned_json_path: Path,
        cleaned_text_path: Path,
        metadata_path: Path,
    ) -> TranscriptCleanupResult | None:
        existing = [
            cleaned_json_path.exists(),
            cleaned_text_path.exists(),
            metadata_path.exists(),
        ]
        if any(existing) and not all(existing):
            raise CleanupStateError(
                "Meeting package contains an inconsistent cleanup state."
            )
        if not any(existing):
            return None

        try:
            transcript = CleanedTranscript.model_validate_json(
                cleaned_json_path.read_text(encoding="utf-8")
            )
            metadata = CleanupMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
            self._validate_existing_cleanup_metadata(
                context,
                transcript,
                metadata,
                cleaned_json_path,
                cleaned_text_path,
            )
        except (OSError, ValidationError, ValueError, TranscriptCleanupError) as exc:
            if isinstance(exc, CleanupStateError):
                raise
            raise CleanupStateError(
                "Meeting package contains an inconsistent cleanup state."
            ) from exc

        return TranscriptCleanupResult(
            meeting_id=context.meeting_id,
            meeting_dir=context.meeting_dir.resolve(strict=False),
            cleaned_json_path=cleaned_json_path.resolve(strict=False),
            cleaned_text_path=cleaned_text_path.resolve(strict=False),
            cleanup_metadata_path=metadata_path.resolve(strict=False),
            transcript=transcript,
            metadata=metadata,
            reused_existing=True,
        )

    def _validate_existing_cleanup_metadata(
        self,
        context: _CleanupContext,
        transcript: CleanedTranscript,
        metadata: CleanupMetadata,
        cleaned_json_path: Path,
        cleaned_text_path: Path,
    ) -> None:
        if transcript.meeting_id != context.meeting_id or metadata.meeting_id != context.meeting_id:
            raise CleanupStateError("Cleanup meeting ID does not match.")
        if transcript.source_raw_transcript_sha256 != context.raw_json_sha256:
            raise CleanupStateError("Cleanup source raw transcript checksum does not match.")
        if transcript.prompt_version != CLEANUP_PROMPT_VERSION:
            raise CleanupStateError("Cleaned transcript prompt version does not match.")
        if metadata.prompt_version != CLEANUP_PROMPT_VERSION:
            raise CleanupStateError("Cleanup prompt version does not match.")
        if metadata.provider.name != CLEANUP_PROVIDER_NAME:
            raise CleanupStateError("Cleanup provider does not match.")
        if metadata.provider.model != self._settings.cleanup_model:
            raise CleanupStateError("Cleanup model does not match.")
        if metadata.provider.response_format_name != CLEANUP_RESPONSE_FORMAT_NAME:
            raise CleanupStateError("Cleanup response schema does not match.")
        if metadata.provider.store is not False:
            raise CleanupStateError("Cleanup store flag does not match.")
        if metadata.provider.strict_schema is not True:
            raise CleanupStateError("Cleanup strict schema flag does not match.")

        self._validate_metadata_input(context, metadata.input)
        cleaned_json_size, cleaned_json_sha = self._inspect_file(
            cleaned_json_path,
            CleanupStateError,
        )
        cleaned_text_size, cleaned_text_sha = self._inspect_file(
            cleaned_text_path,
            CleanupStateError,
        )
        if metadata.artifacts.structured_size_bytes != cleaned_json_size:
            raise CleanupStateError("Cleaned JSON size does not match metadata.")
        if metadata.artifacts.structured_sha256 != cleaned_json_sha:
            raise CleanupStateError("Cleaned JSON checksum does not match metadata.")
        if metadata.artifacts.text_size_bytes != cleaned_text_size:
            raise CleanupStateError("Cleaned text size does not match metadata.")
        if metadata.artifacts.text_sha256 != cleaned_text_sha:
            raise CleanupStateError("Cleaned text checksum does not match metadata.")
        if metadata.artifacts.segment_count != len(transcript.segments):
            raise CleanupStateError("Cleaned segment count does not match metadata.")
        if metadata.artifacts.changed_segment_count != transcript.changed_segment_count:
            raise CleanupStateError("Changed segment count does not match metadata.")
        if metadata.artifacts.unchanged_segment_count != transcript.unchanged_segment_count:
            raise CleanupStateError("Unchanged segment count does not match metadata.")

        rendered = render_cleaned_text(transcript)
        if cleaned_text_path.read_text(encoding="utf-8") != rendered:
            raise CleanupStateError("Cleaned text is not the deterministic rendering.")
        if transcript.text != self._combined_cleaned_text(transcript.segments):
            raise CleanupStateError("Cleaned transcript text is not locally assembled.")
        self._validate_cleaned_transcript_against_raw(
            context.raw_transcript,
            transcript,
            error_type=CleanupStateError,
        )

    def _validate_metadata_input(
        self,
        context: _CleanupContext,
        metadata_input: CleanupInputMetadata,
    ) -> None:
        if metadata_input.raw_text_size_bytes != context.raw_text_size_bytes:
            raise CleanupStateError("Cleanup raw text size does not match.")
        if metadata_input.raw_text_sha256 != context.raw_text_sha256:
            raise CleanupStateError("Cleanup raw text checksum does not match.")
        if metadata_input.raw_structured_size_bytes != context.raw_json_size_bytes:
            raise CleanupStateError("Cleanup raw JSON size does not match.")
        if metadata_input.raw_structured_sha256 != context.raw_json_sha256:
            raise CleanupStateError("Cleanup raw JSON checksum does not match.")
        if metadata_input.raw_segment_count != len(context.raw_transcript.segments):
            raise CleanupStateError("Cleanup raw segment count does not match.")
        if metadata_input.raw_speaker_labels != context.raw_transcript.speaker_labels:
            raise CleanupStateError("Cleanup raw speaker labels do not match.")
        if (
            metadata_input.transcription_metadata_size_bytes
            != context.transcription_metadata_size_bytes
        ):
            raise CleanupStateError("Transcription metadata size does not match.")
        if (
            metadata_input.transcription_metadata_sha256
            != context.transcription_metadata_sha256
        ):
            raise CleanupStateError("Transcription metadata checksum does not match.")
        if metadata_input.normalized_audio_size_bytes != context.normalized_audio_size_bytes:
            raise CleanupStateError("Normalized audio size does not match cleanup input.")
        if metadata_input.normalized_audio_sha256 != context.normalized_audio_sha256:
            raise CleanupStateError("Normalized audio checksum does not match cleanup input.")
        if (
            metadata_input.normalization_metadata_size_bytes
            != context.normalization_metadata_size_bytes
        ):
            raise CleanupStateError("Normalization metadata size does not match.")
        if (
            metadata_input.normalization_metadata_sha256
            != context.normalization_metadata_sha256
        ):
            raise CleanupStateError("Normalization metadata checksum does not match.")

    def _build_batches(
        self,
        segments: list[TranscriptSegment],
    ) -> list[list[TranscriptSegment]]:
        batches: list[list[TranscriptSegment]] = []
        current: list[TranscriptSegment] = []
        limit = self._settings.cleanup_max_batch_characters

        for segment in segments:
            candidate = [*current, segment]
            if current and self._serialized_batch_length(candidate) > limit:
                batches.append(current)
                current = [segment]
            else:
                current = candidate

        if current:
            batches.append(current)
        return batches

    def _serialized_batch_length(self, segments: list[TranscriptSegment]) -> int:
        payload = {
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "speaker_label": segment.speaker_label,
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                    "text": segment.text,
                }
                for segment in segments
            ]
        }
        return len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )

    def _provider_request(
        self,
        context: _CleanupContext,
        batch: list[TranscriptSegment],
        batch_index: int,
        batch_count: int,
    ) -> CleanupProviderRequest:
        return CleanupProviderRequest(
            meeting_id=context.meeting_id,
            model=self._settings.cleanup_model,
            prompt_version=CLEANUP_PROMPT_VERSION,
            response_format_name=CLEANUP_RESPONSE_FORMAT_NAME,
            batch_index=batch_index,
            batch_count=batch_count,
            max_output_tokens=self._settings.cleanup_max_output_tokens,
            segments=[
                CleanupProviderSegment(
                    segment_id=segment.segment_id,
                    speaker_label=segment.speaker_label,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    text=segment.text,
                )
                for segment in batch
            ],
        )

    def _validate_provider_result_for_batch(
        self,
        batch: list[TranscriptSegment],
        result: CleanupProviderResult,
    ) -> None:
        expected_ids = [segment.segment_id for segment in batch]
        actual_ids = [segment.segment_id for segment in result.segments]
        if actual_ids != expected_ids:
            raise CleanupProviderResponseError(
                "Cleanup provider segment IDs do not match the batch."
            )
        if len(set(actual_ids)) != len(actual_ids):
            raise CleanupProviderResponseError(
                "Cleanup provider returned duplicate segment IDs."
            )
        raw_by_id = {segment.segment_id: segment.text for segment in batch}
        for segment in result.segments:
            if raw_by_id[segment.segment_id].strip() and not segment.cleaned_text.strip():
                raise CleanupProviderResponseError(
                    "Cleanup provider returned empty cleaned text."
                )

    def _build_cleaned_transcript(
        self,
        context: _CleanupContext,
        provider_segments: list[CleanupProviderSegmentResult],
    ) -> CleanedTranscript:
        raw_segments = context.raw_transcript.segments
        if not raw_segments:
            return CleanedTranscript(
                meeting_id=context.meeting_id,
                source_raw_transcript_sha256=context.raw_json_sha256,
                text="",
                duration_seconds=context.raw_transcript.duration_seconds,
                segments=[],
            )

        cleaned_by_id = {
            segment.segment_id: segment.cleaned_text for segment in provider_segments
        }
        cleaned_segments: list[CleanedTranscriptSegment] = []
        for raw_segment in raw_segments:
            try:
                cleaned_text = cleaned_by_id[raw_segment.segment_id]
            except KeyError as exc:
                raise CleanupProviderResponseError(
                    "Cleanup provider omitted a raw transcript segment."
                ) from exc
            cleaned_segments.append(
                CleanedTranscriptSegment(
                    segment_id=raw_segment.segment_id,
                    start_seconds=raw_segment.start_seconds,
                    end_seconds=raw_segment.end_seconds,
                    speaker_label=raw_segment.speaker_label,
                    raw_text_sha256=self._hash_text(raw_segment.text),
                    cleaned_text=cleaned_text,
                    changed=cleaned_text != raw_segment.text,
                )
            )

        return CleanedTranscript(
            meeting_id=context.meeting_id,
            source_raw_transcript_sha256=context.raw_json_sha256,
            text=self._combined_cleaned_text(cleaned_segments),
            duration_seconds=context.raw_transcript.duration_seconds,
            segments=cleaned_segments,
        )

    def _validate_cleaned_transcript_against_raw(
        self,
        raw: RawTranscript,
        cleaned: CleanedTranscript,
        *,
        error_type: type[TranscriptCleanupError] = CleanupFidelityError,
    ) -> None:
        if cleaned.duration_seconds != raw.duration_seconds:
            raise error_type("Cleaned transcript duration does not match raw transcript.")
        if len(cleaned.segments) != len(raw.segments):
            raise error_type("Cleaned segment count does not match raw segment count.")

        for raw_segment, cleaned_segment in zip(raw.segments, cleaned.segments, strict=True):
            if cleaned_segment.segment_id != raw_segment.segment_id:
                raise error_type("Cleaned segment ID does not match raw segment ID.")
            if cleaned_segment.start_seconds != raw_segment.start_seconds:
                raise error_type("Cleaned segment start timestamp changed.")
            if cleaned_segment.end_seconds != raw_segment.end_seconds:
                raise error_type("Cleaned segment end timestamp changed.")
            if cleaned_segment.speaker_label != raw_segment.speaker_label:
                raise error_type("Cleaned segment speaker label changed.")
            if cleaned_segment.raw_text_sha256 != self._hash_text(raw_segment.text):
                raise error_type("Cleaned segment raw text hash is invalid.")
            if cleaned_segment.changed != (cleaned_segment.cleaned_text != raw_segment.text):
                raise error_type("Cleaned segment changed flag is invalid.")
            self._validate_segment_fidelity(
                raw_segment.text,
                cleaned_segment.cleaned_text,
                error_type,
            )

        if cleaned.text != self._combined_cleaned_text(cleaned.segments):
            raise error_type("Cleaned transcript text is not locally assembled.")

    def _validate_segment_fidelity(
        self,
        raw_text: str,
        cleaned_text: str,
        error_type: type[TranscriptCleanupError],
    ) -> None:
        if raw_text.strip() and not cleaned_text.strip():
            raise error_type("Nonempty raw segment cannot become empty.")
        try:
            cleaned_text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise error_type("Cleaned text is not valid UTF-8.") from exc
        if len(cleaned_text) > max(
            len(raw_text) * MAX_EXPANSION_MULTIPLIER + MAX_EXPANSION_EXTRA_CHARS,
            MAX_EXPANSION_EXTRA_CHARS,
        ):
            raise error_type("Cleaned segment expands excessively.")
        if self._protected_tokens(raw_text) != self._protected_tokens(cleaned_text):
            raise error_type("Protected transcript tokens changed during cleanup.")

    def _protected_tokens(self, value: str) -> Counter[str]:
        tokens: list[str] = []
        for match in PROTECTED_TOKEN_PATTERN.finditer(value):
            token = match.group(0).strip(TOKEN_EDGE_PUNCTUATION)
            if token:
                tokens.append(token)
        return Counter(tokens)

    def _build_metadata(
        self,
        context: _CleanupContext,
        transcript: CleanedTranscript,
        cleaned_json_size: int,
        cleaned_json_sha: str,
        cleaned_text_size: int,
        cleaned_text_sha: str,
        *,
        batch_count: int,
        provider_request_count: int,
        usage_values: list[CleanupUsage],
    ) -> CleanupMetadata:
        return CleanupMetadata(
            meeting_id=context.meeting_id,
            created_at_utc=self._utc_now(),
            prompt_version=CLEANUP_PROMPT_VERSION,
            provider=CleanupProviderMetadata(
                model=self._settings.cleanup_model,
            ),
            input=CleanupInputMetadata(
                raw_text_size_bytes=context.raw_text_size_bytes,
                raw_text_sha256=context.raw_text_sha256,
                raw_structured_size_bytes=context.raw_json_size_bytes,
                raw_structured_sha256=context.raw_json_sha256,
                raw_segment_count=len(context.raw_transcript.segments),
                raw_speaker_labels=context.raw_transcript.speaker_labels,
                transcription_metadata_size_bytes=(
                    context.transcription_metadata_size_bytes
                ),
                transcription_metadata_sha256=(
                    context.transcription_metadata_sha256
                ),
                normalized_audio_size_bytes=context.normalized_audio_size_bytes,
                normalized_audio_sha256=context.normalized_audio_sha256,
                normalization_metadata_size_bytes=(
                    context.normalization_metadata_size_bytes
                ),
                normalization_metadata_sha256=(
                    context.normalization_metadata_sha256
                ),
            ),
            artifacts=CleanupArtifactsMetadata(
                text_size_bytes=cleaned_text_size,
                text_sha256=cleaned_text_sha,
                structured_size_bytes=cleaned_json_size,
                structured_sha256=cleaned_json_sha,
                segment_count=len(transcript.segments),
                changed_segment_count=transcript.changed_segment_count,
                unchanged_segment_count=transcript.unchanged_segment_count,
            ),
            batching=CleanupBatchingMetadata(
                max_batch_characters=self._settings.cleanup_max_batch_characters,
                batch_count=batch_count,
                provider_request_count=provider_request_count,
            ),
            usage=self._aggregate_usage(usage_values),
        )

    def _aggregate_usage(self, values: list[CleanupUsage]) -> CleanupUsage | None:
        if not values:
            return None

        return CleanupUsage(
            input_tokens=self._sum_optional_usage(values, "input_tokens"),
            output_tokens=self._sum_optional_usage(values, "output_tokens"),
            total_tokens=self._sum_optional_usage(values, "total_tokens"),
            cached_input_tokens=self._sum_optional_usage(values, "cached_input_tokens"),
            reasoning_tokens=self._sum_optional_usage(values, "reasoning_tokens"),
        )

    def _sum_optional_usage(
        self,
        values: list[CleanupUsage],
        field_name: str,
    ) -> int | None:
        observed = [
            getattr(value, field_name)
            for value in values
            if getattr(value, field_name) is not None
        ]
        if not observed:
            return None
        return sum(observed)

    def _write_json_atomically(
        self,
        model: CleanedTranscript | CleanupMetadata,
        path: Path,
        error_type: type[TranscriptCleanupError],
        message: str,
    ) -> None:
        payload = model.model_dump_json(indent=2) + "\n"
        self._write_text_atomically(payload, path, error_type, message)

    def _write_text_atomically(
        self,
        payload: str,
        path: Path,
        error_type: type[TranscriptCleanupError],
        message: str,
    ) -> None:
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove temporary cleanup artifact")
            raise error_type(message) from exc

    def _publish_artifacts(
        self,
        staged_cleaned_json_path: Path,
        cleaned_json_path: Path,
        staged_cleaned_text_path: Path,
        cleaned_text_path: Path,
        staged_metadata_path: Path,
        metadata_path: Path,
    ) -> None:
        published: list[Path] = []
        try:
            cleaned_json_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if cleaned_json_path.exists() or cleaned_text_path.exists() or metadata_path.exists():
                raise CleanupStateError(
                    "Meeting package contains an inconsistent cleanup state."
                )

            os.replace(staged_cleaned_json_path, cleaned_json_path)
            published.append(cleaned_json_path)
            os.replace(staged_cleaned_text_path, cleaned_text_path)
            published.append(cleaned_text_path)
            if metadata_path.exists():
                raise CleanupStateError(
                    "Meeting package contains an inconsistent cleanup state."
                )
            os.replace(staged_metadata_path, metadata_path)
            published.append(metadata_path)
        except CleanupStateError:
            self._remove_published_artifacts(published)
            raise
        except OSError as exc:
            self._remove_published_artifacts(published)
            raise CleanupPublicationError(
                "Cleanup artifacts could not be published."
            ) from exc

    def _remove_published_artifacts(self, paths: list[Path]) -> None:
        logger.info("Attempting cleanup artifact rollback")
        for path in reversed(paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Rollback failed for published cleanup artifact")

    def _rollback_staging(self, staging_dir: Path) -> None:
        try:
            if staging_dir.exists():
                logger.info("Rolling back staged cleanup artifacts")
                shutil.rmtree(staging_dir)
            staging_root = staging_dir.parent
            if staging_root.exists() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            logger.exception("Rollback failed for staged cleanup artifacts")

    def _resolve_package_relative_path(
        self,
        package_root: Path,
        relative_path: str,
        error_type: type[TranscriptCleanupError],
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
        error_type: type[TranscriptCleanupError],
    ) -> None:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts or "\\" in relative_path:
            raise error_type("Package-relative path is unsafe.")

    def _inspect_file(
        self,
        path: Path,
        error_type: type[TranscriptCleanupError],
    ) -> tuple[int, str]:
        try:
            if not path.exists() or not path.is_file():
                if error_type is CleanupInputIntegrityError:
                    raise CleanupInputNotFoundError("Cleanup input artifact is missing.")
                raise error_type("Cleanup artifact is missing.")
            size_bytes = path.stat().st_size
        except OSError as exc:
            raise error_type("Cleanup artifact could not be inspected.") from exc

        if size_bytes <= 0:
            raise error_type("Cleanup artifact is empty.")
        return size_bytes, self._hash_file(path, error_type)

    def _hash_file(
        self,
        path: Path,
        error_type: type[TranscriptCleanupError],
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
