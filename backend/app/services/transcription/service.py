"""Canonical diarized raw transcription publication service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.models.meeting import MeetingManifest
from backend.app.models.normalization import (
    CANONICAL_NORMALIZATION_PROFILE,
    NORMALIZATION_CHANNELS,
    NORMALIZATION_CODEC,
    NORMALIZATION_PROFILE_ID,
    NORMALIZATION_SAMPLE_FORMAT,
    NORMALIZATION_SAMPLE_RATE_HZ,
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
    TranscriptionArtifactsMetadata,
    TranscriptionInputMetadata,
    TranscriptionMetadata,
    TranscriptionProviderMetadata,
    TranscriptionResult,
    render_raw_text,
)
from backend.app.services.transcription.errors import (
    TranscriptionError,
    TranscriptionInputIntegrityError,
    TranscriptionInputNotFoundError,
    TranscriptionMetadataWriteError,
    TranscriptionProviderResponseError,
    TranscriptionPublicationError,
    TranscriptionStateError,
)
from backend.app.services.transcription.openai_provider import OpenAITranscriptionProvider
from backend.app.services.transcription.provider import (
    TranscriptionProvider,
    TranscriptionProviderRequest,
    TranscriptionProviderResult,
)

logger = logging.getLogger(__name__)

MEETING_ID_PATTERN = re.compile(
    r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$",
)
HASH_BUFFER_SIZE = 1024 * 1024

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class _TranscriptionContext:
    meeting_id: str
    meeting_dir: Path
    manifest: MeetingManifest
    normalization: NormalizationMetadata
    normalized_audio_path: Path
    normalized_size_bytes: int
    normalized_sha256: str


class TranscriptionService:
    """Publish provider-independent raw transcription artifacts."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: TranscriptionProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def transcribe_meeting(self, meeting_id: str) -> TranscriptionResult:
        logger.info("Starting raw transcription for meeting %s", meeting_id)

        context = self._load_context(meeting_id)
        logger.info("Normalized audio integrity verified for meeting %s", meeting_id)

        raw_json_path = context.meeting_dir / TRANSCRIPT_JSON_RELATIVE_PATH
        raw_text_path = context.meeting_dir / TRANSCRIPT_TEXT_RELATIVE_PATH
        metadata_path = context.meeting_dir / TRANSCRIPTION_METADATA_RELATIVE_PATH

        existing_result = self._reuse_existing_if_valid(
            context,
            raw_json_path,
            raw_text_path,
            metadata_path,
        )
        if existing_result is not None:
            logger.info("Reusing existing transcription for meeting %s", meeting_id)
            return existing_result

        staging_dir = context.meeting_dir / ".staging" / f"transcription_{uuid.uuid4().hex}"

        try:
            staging_transcript_dir = staging_dir / "transcript"
            staging_transcript_dir.mkdir(parents=True, exist_ok=False)
            staged_raw_json_path = staging_transcript_dir / "raw.json"
            staged_raw_text_path = staging_transcript_dir / "raw.txt"
            staged_metadata_path = staging_dir / "transcription.json"

            provider = self._provider or OpenAITranscriptionProvider(self._settings)
            request = TranscriptionProviderRequest(
                meeting_id=context.meeting_id,
                audio_path=context.normalized_audio_path,
                model=self._settings.transcription_model,
                response_format=TRANSCRIPTION_RESPONSE_FORMAT,
                chunking_strategy=TRANSCRIPTION_CHUNKING_STRATEGY,
                language=self._settings.transcription_language,
            )

            logger.info("Requesting provider transcription for meeting %s", meeting_id)
            provider_result = provider.transcribe(request)
            transcript = self._build_raw_transcript(context.meeting_id, provider_result)
            logger.info(
                "Provider transcription completed for meeting %s with %s segments",
                meeting_id,
                len(transcript.segments),
            )

            self._write_json_atomically(
                transcript,
                staged_raw_json_path,
                TranscriptionPublicationError,
                "Raw transcript JSON could not be written.",
            )
            raw_text = render_raw_text(transcript)
            self._write_text_atomically(
                raw_text,
                staged_raw_text_path,
                TranscriptionPublicationError,
                "Raw transcript text could not be written.",
            )

            raw_json_size, raw_json_sha = self._inspect_file(staged_raw_json_path)
            raw_text_size, raw_text_sha = self._inspect_file(staged_raw_text_path)
            metadata = TranscriptionMetadata(
                meeting_id=context.meeting_id,
                created_at_utc=self._utc_now(),
                provider=TranscriptionProviderMetadata(
                    requested_language=self._settings.transcription_language,
                ),
                input=TranscriptionInputMetadata(
                    size_bytes=context.normalized_size_bytes,
                    sha256=context.normalized_sha256,
                    duration_seconds=context.normalization.output.duration_seconds,
                ),
                transcript=TranscriptionArtifactsMetadata(
                    text_size_bytes=raw_text_size,
                    text_sha256=raw_text_sha,
                    structured_size_bytes=raw_json_size,
                    structured_sha256=raw_json_sha,
                    segment_count=len(transcript.segments),
                    speaker_labels=transcript.speaker_labels,
                ),
                usage=provider_result.usage,
            )
            self._write_json_atomically(
                metadata,
                staged_metadata_path,
                TranscriptionMetadataWriteError,
                "Transcription metadata could not be written.",
            )
            self._publish_artifacts(
                staging_transcript_dir,
                context.meeting_dir / "transcript",
                staged_metadata_path,
                metadata_path,
            )

            logger.info(
                "Completed raw transcription for meeting %s with %s speaker labels",
                meeting_id,
                len(transcript.speaker_labels),
            )
            return TranscriptionResult(
                meeting_id=context.meeting_id,
                meeting_dir=context.meeting_dir.resolve(strict=False),
                raw_text_path=raw_text_path.resolve(strict=False),
                raw_json_path=raw_json_path.resolve(strict=False),
                transcription_metadata_path=metadata_path.resolve(strict=False),
                transcript=transcript,
                metadata=metadata,
                reused_existing=False,
            )
        except TranscriptionError:
            logger.info("Raw transcription failed for meeting %s", meeting_id)
            raise
        except OSError as exc:
            logger.info("Raw transcription failed for meeting %s", meeting_id)
            raise TranscriptionPublicationError(
                "Transcription artifacts could not be staged or published."
            ) from exc
        finally:
            self._rollback_staging(staging_dir)

    def _load_context(self, meeting_id: str) -> _TranscriptionContext:
        if not MEETING_ID_PATTERN.fullmatch(meeting_id):
            raise TranscriptionInputIntegrityError("Meeting ID is invalid.")

        meetings_dir = self._settings.meetings_dir
        meeting_dir = (meetings_dir / meeting_id).resolve(strict=False)
        if not self._is_relative_to(meeting_dir, meetings_dir):
            raise TranscriptionInputNotFoundError("Meeting package path is invalid.")
        if not meeting_dir.exists() or not meeting_dir.is_dir():
            raise TranscriptionInputNotFoundError("Meeting package was not found.")

        manifest = self._load_meeting_manifest(meeting_dir / "metadata" / "meeting.json")
        normalization = self._load_normalization_metadata(
            meeting_dir / "metadata" / "normalization.json"
        )

        if manifest.meeting_id != meeting_id or normalization.meeting_id != meeting_id:
            raise TranscriptionInputIntegrityError(
                "Meeting metadata IDs do not match the requested meeting ID."
            )
        if normalization.profile != CANONICAL_NORMALIZATION_PROFILE:
            raise TranscriptionInputIntegrityError(
                "Normalization profile does not match the canonical profile."
            )
        if normalization.output.relative_path != NORMALIZED_AUDIO_RELATIVE_PATH:
            raise TranscriptionInputIntegrityError(
                "Normalization output path is not canonical."
            )
        if normalization.input.sha256 != manifest.source.sha256:
            raise TranscriptionInputIntegrityError(
                "Normalization input checksum does not match the source manifest."
            )
        if normalization.output.codec != NORMALIZATION_CODEC:
            raise TranscriptionInputIntegrityError("Normalized codec is not canonical.")
        if normalization.output.sample_rate_hz != NORMALIZATION_SAMPLE_RATE_HZ:
            raise TranscriptionInputIntegrityError(
                "Normalized sample rate is not canonical."
            )
        if normalization.output.channels != NORMALIZATION_CHANNELS:
            raise TranscriptionInputIntegrityError(
                "Normalized channel count is not canonical."
            )
        if normalization.output.sample_format != NORMALIZATION_SAMPLE_FORMAT:
            raise TranscriptionInputIntegrityError(
                "Normalized sample format is not canonical."
            )

        normalized_audio_path = self._resolve_package_relative_path(
            meeting_dir,
            normalization.output.relative_path,
            TranscriptionInputIntegrityError,
        )
        try:
            if not normalized_audio_path.exists() or not normalized_audio_path.is_file():
                raise TranscriptionInputNotFoundError(
                    "Normalized audio file was not found."
                )
            normalized_size = normalized_audio_path.stat().st_size
        except TranscriptionInputNotFoundError:
            raise
        except OSError as exc:
            raise TranscriptionInputIntegrityError(
                "Normalized audio could not be inspected."
            ) from exc

        if normalized_size != normalization.output.size_bytes:
            raise TranscriptionInputIntegrityError(
                "Normalized audio size does not match normalization metadata."
            )
        normalized_sha = self._hash_file(normalized_audio_path)
        if normalized_sha != normalization.output.sha256:
            raise TranscriptionInputIntegrityError(
                "Normalized audio checksum does not match normalization metadata."
            )

        return _TranscriptionContext(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            manifest=manifest,
            normalization=normalization,
            normalized_audio_path=normalized_audio_path,
            normalized_size_bytes=normalized_size,
            normalized_sha256=normalized_sha,
        )

    def _load_meeting_manifest(self, manifest_path: Path) -> MeetingManifest:
        if not manifest_path.exists():
            raise TranscriptionInputNotFoundError("Meeting manifest was not found.")
        try:
            return MeetingManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise TranscriptionInputIntegrityError(
                "Meeting manifest is invalid for transcription."
            ) from exc

    def _load_normalization_metadata(
        self,
        metadata_path: Path,
    ) -> NormalizationMetadata:
        if not metadata_path.exists():
            raise TranscriptionInputNotFoundError(
                "Normalization metadata was not found."
            )
        try:
            return NormalizationMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise TranscriptionInputIntegrityError(
                "Normalization metadata is invalid for transcription."
            ) from exc

    def _reuse_existing_if_valid(
        self,
        context: _TranscriptionContext,
        raw_json_path: Path,
        raw_text_path: Path,
        metadata_path: Path,
    ) -> TranscriptionResult | None:
        existing = [raw_json_path.exists(), raw_text_path.exists(), metadata_path.exists()]
        if any(existing) and not all(existing):
            raise TranscriptionStateError(
                "Meeting package contains an inconsistent transcription state."
            )
        if not any(existing):
            return None

        try:
            transcript = RawTranscript.model_validate_json(
                raw_json_path.read_text(encoding="utf-8")
            )
            metadata = TranscriptionMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
            self._validate_existing_metadata(
                context,
                transcript,
                metadata,
                raw_json_path,
                raw_text_path,
            )
        except (OSError, ValidationError, ValueError, TranscriptionError) as exc:
            if isinstance(exc, TranscriptionStateError):
                raise
            raise TranscriptionStateError(
                "Meeting package contains an inconsistent transcription state."
            ) from exc

        return TranscriptionResult(
            meeting_id=context.meeting_id,
            meeting_dir=context.meeting_dir.resolve(strict=False),
            raw_text_path=raw_text_path.resolve(strict=False),
            raw_json_path=raw_json_path.resolve(strict=False),
            transcription_metadata_path=metadata_path.resolve(strict=False),
            transcript=transcript,
            metadata=metadata,
            reused_existing=True,
        )

    def _validate_existing_metadata(
        self,
        context: _TranscriptionContext,
        transcript: RawTranscript,
        metadata: TranscriptionMetadata,
        raw_json_path: Path,
        raw_text_path: Path,
    ) -> None:
        if transcript.meeting_id != context.meeting_id or metadata.meeting_id != context.meeting_id:
            raise TranscriptionStateError("Transcription meeting ID does not match.")
        if metadata.provider.name != TRANSCRIPTION_PROVIDER_NAME:
            raise TranscriptionStateError("Transcription provider does not match.")
        if metadata.provider.model != self._settings.transcription_model:
            raise TranscriptionStateError("Transcription model does not match.")
        if metadata.provider.response_format != TRANSCRIPTION_RESPONSE_FORMAT:
            raise TranscriptionStateError("Transcription response format does not match.")
        if metadata.provider.chunking_strategy != TRANSCRIPTION_CHUNKING_STRATEGY:
            raise TranscriptionStateError("Transcription chunking strategy does not match.")
        if metadata.provider.requested_language != self._settings.transcription_language:
            raise TranscriptionStateError("Transcription language does not match.")
        if metadata.input.relative_path != NORMALIZED_AUDIO_RELATIVE_PATH:
            raise TranscriptionStateError("Transcription input path does not match.")
        if metadata.input.size_bytes != context.normalized_size_bytes:
            raise TranscriptionStateError("Transcription input size does not match.")
        if metadata.input.sha256 != context.normalized_sha256:
            raise TranscriptionStateError("Transcription input checksum does not match.")
        if metadata.input.normalization_profile != NORMALIZATION_PROFILE_ID:
            raise TranscriptionStateError("Transcription normalization profile does not match.")

        raw_json_size, raw_json_sha = self._inspect_file(raw_json_path)
        raw_text_size, raw_text_sha = self._inspect_file(raw_text_path)
        if metadata.transcript.structured_size_bytes != raw_json_size:
            raise TranscriptionStateError("Raw JSON size does not match metadata.")
        if metadata.transcript.structured_sha256 != raw_json_sha:
            raise TranscriptionStateError("Raw JSON checksum does not match metadata.")
        if metadata.transcript.text_size_bytes != raw_text_size:
            raise TranscriptionStateError("Raw text size does not match metadata.")
        if metadata.transcript.text_sha256 != raw_text_sha:
            raise TranscriptionStateError("Raw text checksum does not match metadata.")
        if metadata.transcript.segment_count != len(transcript.segments):
            raise TranscriptionStateError("Transcript segment count does not match metadata.")
        if metadata.transcript.speaker_labels != transcript.speaker_labels:
            raise TranscriptionStateError("Transcript speaker labels do not match metadata.")
        if raw_text_path.read_text(encoding="utf-8") != render_raw_text(transcript):
            raise TranscriptionStateError("Raw text is not the deterministic rendering.")

    def _build_raw_transcript(
        self,
        meeting_id: str,
        provider_result: TranscriptionProviderResult,
    ) -> RawTranscript:
        try:
            return RawTranscript(
                meeting_id=meeting_id,
                text=provider_result.text,
                duration_seconds=provider_result.duration_seconds,
                segments=[
                    TranscriptSegment(
                        segment_id=segment.segment_id or f"seg_{index:03d}",
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        speaker_label=segment.speaker_label,
                        text=segment.text,
                    )
                    for index, segment in enumerate(provider_result.segments, start=1)
                ],
            )
        except (AttributeError, TypeError, ValidationError, ValueError) as exc:
            raise TranscriptionProviderResponseError(
                "Transcription provider response was invalid."
            ) from exc

    def _write_json_atomically(
        self,
        model: RawTranscript | TranscriptionMetadata,
        path: Path,
        error_type: type[TranscriptionError],
        message: str,
    ) -> None:
        payload = model.model_dump_json(indent=2) + "\n"
        self._write_text_atomically(payload, path, error_type, message)

    def _write_text_atomically(
        self,
        payload: str,
        path: Path,
        error_type: type[TranscriptionError],
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
                logger.exception("Failed to remove temporary transcription artifact")
            raise error_type(message) from exc

    def _publish_artifacts(
        self,
        staged_transcript_dir: Path,
        transcript_dir: Path,
        staged_metadata_path: Path,
        metadata_path: Path,
    ) -> None:
        published_transcript = False
        try:
            transcript_dir.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if transcript_dir.exists() or metadata_path.exists():
                raise TranscriptionStateError(
                    "Meeting package contains an inconsistent transcription state."
                )

            staged_transcript_dir.rename(transcript_dir)
            published_transcript = True

            if metadata_path.exists():
                raise TranscriptionStateError(
                    "Meeting package contains an inconsistent transcription state."
                )
            os.replace(staged_metadata_path, metadata_path)
        except TranscriptionStateError:
            if published_transcript:
                self._remove_published_transcript(transcript_dir)
            raise
        except OSError as exc:
            if published_transcript:
                self._remove_published_transcript(transcript_dir)
            raise TranscriptionPublicationError(
                "Transcription artifacts could not be published."
            ) from exc

    def _remove_published_transcript(self, transcript_dir: Path) -> None:
        try:
            if transcript_dir.exists():
                shutil.rmtree(transcript_dir)
        except OSError:
            logger.exception("Failed to remove transcript artifacts after metadata failure")

    def _rollback_staging(self, staging_dir: Path) -> None:
        try:
            if staging_dir.exists():
                logger.info("Rolling back staged transcription artifacts")
                shutil.rmtree(staging_dir)
            staging_root = staging_dir.parent
            if staging_root.exists() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            logger.exception("Rollback failed for staged transcription artifacts")

    def _resolve_package_relative_path(
        self,
        package_root: Path,
        relative_path: str,
        error_type: type[TranscriptionError],
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
        error_type: type[TranscriptionError],
    ) -> None:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts or "\\" in relative_path:
            raise error_type("Package-relative path is unsafe.")

    def _inspect_file(self, path: Path) -> tuple[int, str]:
        try:
            if not path.exists() or not path.is_file():
                raise TranscriptionStateError("Transcription artifact is missing.")
            size_bytes = path.stat().st_size
        except TranscriptionStateError:
            raise
        except OSError as exc:
            raise TranscriptionStateError("Transcription artifact could not be inspected.") from exc

        if size_bytes <= 0:
            raise TranscriptionStateError("Transcription artifact is empty.")
        return size_bytes, self._hash_file(path)

    def _hash_file(self, path: Path) -> str:
        checksum = hashlib.sha256()
        try:
            with path.open("rb") as file:
                while chunk := file.read(HASH_BUFFER_SIZE):
                    checksum.update(chunk)
        except OSError as exc:
            raise TranscriptionInputIntegrityError("Artifact could not be hashed.") from exc
        return checksum.hexdigest()

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
