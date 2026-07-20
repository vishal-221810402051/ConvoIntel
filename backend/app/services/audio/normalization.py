"""Canonical audio normalization for Phase 2 meeting packages."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.models.meeting import MeetingManifest
from backend.app.models.normalization import (
    CANONICAL_NORMALIZATION_PROFILE,
    NORMALIZATION_CHANNELS,
    NORMALIZATION_CODEC,
    NORMALIZATION_MEDIA_TYPE,
    NORMALIZATION_SAMPLE_FORMAT,
    NORMALIZATION_SAMPLE_RATE_HZ,
    NORMALIZED_AUDIO_RELATIVE_PATH,
    AudioNormalizationResult,
    NormalizationInputMetadata,
    NormalizationMetadata,
    NormalizationOutputMetadata,
    NormalizationToolMetadata,
)
from backend.app.services.audio.normalization_errors import (
    AudioNormalizationError,
    FfmpegNotAvailableError,
    FfprobeNotAvailableError,
    MeetingManifestInvalidError,
    MeetingManifestNotFoundError,
    MeetingPackageNotFoundError,
    NormalizationMetadataWriteError,
    NormalizationProcessError,
    NormalizationPublicationError,
    NormalizationStateError,
    NormalizationTimeoutError,
    NormalizedAudioValidationError,
    SourceAudioIntegrityError,
    SourceAudioMissingError,
)

logger = logging.getLogger(__name__)

MEETING_ID_PATTERN = re.compile(
    r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$",
)
HASH_BUFFER_SIZE = 1024 * 1024
SIGNED_16_SAMPLE_FORMATS = {"s16"}
STDERR_SUMMARY_LIMIT = 500


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class CommandResult:
    """Completed subprocess result captured by a command runner."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class SubprocessCommandRunner:
    """Small wrapper around `subprocess.run` for injectable tests."""

    def run(self, args: Sequence[str], timeout_seconds: int) -> CommandResult:
        command = tuple(args)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            shell=False,
        )
        return CommandResult(
            args=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


@dataclass(frozen=True)
class _MeetingContext:
    meeting_id: str
    meeting_dir: Path
    manifest_path: Path
    manifest: MeetingManifest
    source_audio_path: Path
    source_size_bytes: int
    source_sha256: str


@dataclass(frozen=True)
class _ProbeResult:
    duration_seconds: float
    size_bytes: int
    codec: str
    sample_rate_hz: int
    channels: int
    sample_format: str


class AudioNormalizationService:
    """Normalize preserved source audio into the canonical WAV artifact."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        command_runner: SubprocessCommandRunner | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._command_runner = command_runner or SubprocessCommandRunner()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def normalize_meeting(self, meeting_id: str) -> AudioNormalizationResult:
        logger.info("Starting audio normalization for meeting %s", meeting_id)

        context = self._load_meeting_context(meeting_id)
        logger.info("Source integrity verified for meeting %s", meeting_id)

        output_path = context.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH
        metadata_path = context.meeting_dir / "metadata" / "normalization.json"
        existing_result = self._reuse_existing_if_valid(
            context,
            output_path,
            metadata_path,
        )
        if existing_result is not None:
            logger.info("Reusing existing normalization for meeting %s", meeting_id)
            return existing_result

        staging_root = context.meeting_dir / ".staging"
        staging_dir = staging_root / f"normalization_{uuid.uuid4().hex}"

        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_output_path = staging_dir / "audio.wav"
            staged_metadata_path = staging_dir / "normalization.json"

            tool_version = self._read_ffmpeg_version()
            self._run_ffmpeg(context.source_audio_path, staged_output_path)
            probe = self._probe_normalized_audio(staged_output_path)
            output_size, output_sha256 = self._inspect_output_file(staged_output_path)

            metadata = NormalizationMetadata(
                meeting_id=context.meeting_id,
                created_at_utc=self._utc_now(),
                profile=CANONICAL_NORMALIZATION_PROFILE,
                input=NormalizationInputMetadata(
                    relative_path=context.manifest.source.relative_path,
                    size_bytes=context.source_size_bytes,
                    sha256=context.source_sha256,
                ),
                output=NormalizationOutputMetadata(
                    size_bytes=output_size,
                    sha256=output_sha256,
                    duration_seconds=probe.duration_seconds,
                    codec=probe.codec,
                    sample_rate_hz=probe.sample_rate_hz,
                    channels=probe.channels,
                    sample_format=probe.sample_format,
                ),
                tool=NormalizationToolMetadata(
                    name=self._tool_name(self._settings.ffmpeg_binary),
                    version=tool_version,
                ),
            )
            self._write_metadata_atomically(metadata, staged_metadata_path)
            self._publish_artifacts(
                staged_output_path,
                output_path,
                staged_metadata_path,
                metadata_path,
            )

            logger.info("Completed audio normalization for meeting %s", meeting_id)
            return AudioNormalizationResult(
                meeting_id=context.meeting_id,
                meeting_dir=context.meeting_dir.resolve(strict=False),
                normalized_audio_path=output_path.resolve(strict=False),
                normalization_metadata_path=metadata_path.resolve(strict=False),
                metadata=metadata,
                reused_existing=False,
            )
        except AudioNormalizationError:
            logger.info("Audio normalization failed for meeting %s", meeting_id)
            raise
        except OSError as exc:
            logger.info("Audio normalization failed for meeting %s", meeting_id)
            raise NormalizationPublicationError(
                "Normalization artifacts could not be staged or published."
            ) from exc
        finally:
            self._rollback_staging(staging_dir)

    def _load_meeting_context(self, meeting_id: str) -> _MeetingContext:
        if not MEETING_ID_PATTERN.fullmatch(meeting_id):
            raise MeetingManifestInvalidError("Meeting ID is invalid.")

        meetings_dir = self._settings.meetings_dir
        meeting_dir = (meetings_dir / meeting_id).resolve(strict=False)
        if not self._is_relative_to(meeting_dir, meetings_dir):
            raise MeetingPackageNotFoundError("Meeting package path is invalid.")
        if not meeting_dir.exists() or not meeting_dir.is_dir():
            raise MeetingPackageNotFoundError("Meeting package was not found.")

        manifest_path = meeting_dir / "metadata" / "meeting.json"
        manifest = self._load_meeting_manifest(manifest_path)
        if manifest.meeting_id != meeting_id:
            raise MeetingManifestInvalidError(
                "Meeting manifest does not match the requested meeting ID."
            )

        source_audio_path = self._resolve_package_relative_path(
            meeting_dir,
            manifest.source.relative_path,
            SourceAudioIntegrityError,
        )
        try:
            if not source_audio_path.exists() or not source_audio_path.is_file():
                raise SourceAudioMissingError("Source audio file was not found.")
            source_size = source_audio_path.stat().st_size
        except SourceAudioMissingError:
            raise
        except OSError as exc:
            raise SourceAudioIntegrityError(
                "Source audio could not be inspected."
            ) from exc

        if source_size != manifest.source.size_bytes:
            raise SourceAudioIntegrityError(
                "Source audio size no longer matches the meeting manifest."
            )

        source_sha256 = self._hash_file(
            source_audio_path,
            SourceAudioIntegrityError,
            "Source audio could not be hashed.",
        )
        if source_sha256 != manifest.source.sha256:
            raise SourceAudioIntegrityError(
                "Source audio checksum no longer matches the meeting manifest."
            )

        return _MeetingContext(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            manifest_path=manifest_path,
            manifest=manifest,
            source_audio_path=source_audio_path,
            source_size_bytes=source_size,
            source_sha256=source_sha256,
        )

    def _load_meeting_manifest(self, manifest_path: Path) -> MeetingManifest:
        if not manifest_path.exists():
            raise MeetingManifestNotFoundError("Meeting manifest was not found.")

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MeetingManifestInvalidError("Meeting manifest is not valid JSON.") from exc
        except OSError as exc:
            raise MeetingManifestInvalidError("Meeting manifest could not be read.") from exc

        relative_path = self._raw_source_relative_path(payload)
        if relative_path is not None:
            self._assert_safe_package_relative_path(
                relative_path,
                SourceAudioIntegrityError,
            )

        try:
            return MeetingManifest.model_validate(payload)
        except ValidationError as exc:
            raise MeetingManifestInvalidError("Meeting manifest is invalid.") from exc

    def _reuse_existing_if_valid(
        self,
        context: _MeetingContext,
        output_path: Path,
        metadata_path: Path,
    ) -> AudioNormalizationResult | None:
        output_exists = output_path.exists()
        metadata_exists = metadata_path.exists()

        if output_exists != metadata_exists:
            raise NormalizationStateError(
                "Meeting package contains an inconsistent normalization state."
            )
        if not output_exists and not metadata_exists:
            return None

        try:
            metadata = self._load_normalization_metadata(metadata_path)
            self._validate_existing_metadata(context, metadata, output_path)
            self._probe_normalized_audio(output_path)
        except FfprobeNotAvailableError:
            raise
        except AudioNormalizationError as exc:
            if isinstance(exc, NormalizationStateError):
                raise
            raise NormalizationStateError(
                "Meeting package contains an inconsistent normalization state."
            ) from exc

        return AudioNormalizationResult(
            meeting_id=context.meeting_id,
            meeting_dir=context.meeting_dir.resolve(strict=False),
            normalized_audio_path=output_path.resolve(strict=False),
            normalization_metadata_path=metadata_path.resolve(strict=False),
            metadata=metadata,
            reused_existing=True,
        )

    def _load_normalization_metadata(self, metadata_path: Path) -> NormalizationMetadata:
        try:
            return NormalizationMetadata.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise NormalizationStateError(
                "Normalization metadata is invalid."
            ) from exc

    def _validate_existing_metadata(
        self,
        context: _MeetingContext,
        metadata: NormalizationMetadata,
        output_path: Path,
    ) -> None:
        if metadata.meeting_id != context.meeting_id:
            raise NormalizationStateError(
                "Normalization metadata meeting ID does not match."
            )
        if metadata.profile != CANONICAL_NORMALIZATION_PROFILE:
            raise NormalizationStateError(
                "Normalization metadata profile does not match the canonical profile."
            )
        if metadata.input.relative_path != context.manifest.source.relative_path:
            raise NormalizationStateError(
                "Normalization metadata input path does not match."
            )
        if metadata.input.size_bytes != context.source_size_bytes:
            raise NormalizationStateError(
                "Normalization metadata input size does not match."
            )
        if metadata.input.sha256 != context.source_sha256:
            raise NormalizationStateError(
                "Normalization metadata input checksum does not match."
            )

        output_size, output_sha256 = self._inspect_output_file(output_path)
        if metadata.output.size_bytes != output_size:
            raise NormalizationStateError(
                "Normalization metadata output size does not match."
            )
        if metadata.output.sha256 != output_sha256:
            raise NormalizationStateError(
                "Normalization metadata output checksum does not match."
            )

    def _read_ffmpeg_version(self) -> str:
        result = self._run_command(
            [self._settings.ffmpeg_binary, "-version"],
            FfmpegNotAvailableError,
        )
        if result.returncode != 0:
            return f"{self._tool_name(self._settings.ffmpeg_binary)} version unavailable"

        first_line = next(
            (line.strip() for line in result.stdout.splitlines() if line.strip()),
            "",
        )
        if not first_line:
            return f"{self._tool_name(self._settings.ffmpeg_binary)} version unavailable"

        return self._sanitize_tool_version(first_line)

    def _run_ffmpeg(self, source_audio_path: Path, staged_output_path: Path) -> None:
        command = [
            self._settings.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_audio_path),
            "-map",
            "0:a:0",
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            str(NORMALIZATION_CHANNELS),
            "-ar",
            str(NORMALIZATION_SAMPLE_RATE_HZ),
            "-c:a",
            NORMALIZATION_CODEC,
            "-f",
            "wav",
            str(staged_output_path),
        ]

        logger.info("Invoking FFmpeg for canonical audio normalization")
        result = self._run_command(command, FfmpegNotAvailableError)
        if result.returncode != 0:
            try:
                staged_output_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove incomplete staged output")
            raise NormalizationProcessError(
                "FFmpeg normalization failed: "
                f"{self._stderr_summary(result.stderr)}"
            )
        logger.info("FFmpeg completed successfully")

    def _probe_normalized_audio(self, output_path: Path) -> _ProbeResult:
        command = [
            self._settings.ffprobe_binary,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index,codec_name,sample_fmt,sample_rate,channels",
            "-show_entries",
            "format=format_name,duration,size",
            "-of",
            "json",
            str(output_path),
        ]

        result = self._run_command(command, FfprobeNotAvailableError)
        if result.returncode != 0:
            raise NormalizedAudioValidationError(
                "FFprobe validation failed: "
                f"{self._stderr_summary(result.stderr)}"
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise NormalizedAudioValidationError(
                "FFprobe returned malformed JSON."
            ) from exc

        probe = self._parse_probe_payload(payload)
        logger.info("Normalized audio validation completed")
        return probe

    def _parse_probe_payload(self, payload: object) -> _ProbeResult:
        if not isinstance(payload, dict):
            raise NormalizedAudioValidationError("FFprobe payload is not an object.")

        streams = payload.get("streams")
        if not isinstance(streams, list):
            raise NormalizedAudioValidationError("FFprobe stream payload is missing.")
        if len(streams) != 1:
            raise NormalizedAudioValidationError(
                "Normalized audio must contain exactly one audio stream."
            )
        stream = streams[0]
        if not isinstance(stream, dict):
            raise NormalizedAudioValidationError("FFprobe stream payload is invalid.")

        codec = str(stream.get("codec_name") or "")
        if codec != NORMALIZATION_CODEC:
            raise NormalizedAudioValidationError(
                "Normalized audio codec does not match the canonical profile."
            )

        sample_rate_hz = self._parse_positive_int(
            stream.get("sample_rate"),
            "Normalized audio sample rate is invalid.",
        )
        if sample_rate_hz != NORMALIZATION_SAMPLE_RATE_HZ:
            raise NormalizedAudioValidationError(
                "Normalized audio sample rate does not match the canonical profile."
            )

        channels = self._parse_positive_int(
            stream.get("channels"),
            "Normalized audio channel count is invalid.",
        )
        if channels != NORMALIZATION_CHANNELS:
            raise NormalizedAudioValidationError(
                "Normalized audio channel count does not match the canonical profile."
            )

        sample_format = str(stream.get("sample_fmt") or "")
        if sample_format not in SIGNED_16_SAMPLE_FORMATS:
            raise NormalizedAudioValidationError(
                "Normalized audio sample format is not signed 16-bit PCM."
            )

        format_payload = payload.get("format")
        if not isinstance(format_payload, dict):
            raise NormalizedAudioValidationError("FFprobe format payload is missing.")

        format_name = str(format_payload.get("format_name") or "")
        if "wav" not in {part.strip() for part in format_name.split(",")}:
            raise NormalizedAudioValidationError("Normalized audio is not a WAV file.")

        duration_seconds = self._parse_non_negative_float(
            format_payload.get("duration"),
            "Normalized audio duration is invalid.",
        )
        size_bytes = self._parse_positive_int(
            format_payload.get("size"),
            "Normalized audio size is invalid.",
        )

        return _ProbeResult(
            duration_seconds=duration_seconds,
            size_bytes=size_bytes,
            codec=codec,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            sample_format=sample_format,
        )

    def _inspect_output_file(self, output_path: Path) -> tuple[int, str]:
        try:
            if not output_path.exists() or not output_path.is_file():
                raise NormalizedAudioValidationError(
                    "Normalized audio file was not created."
                )
            size_bytes = output_path.stat().st_size
        except NormalizedAudioValidationError:
            raise
        except OSError as exc:
            raise NormalizedAudioValidationError(
                "Normalized audio could not be inspected."
            ) from exc

        if size_bytes <= 0:
            raise NormalizedAudioValidationError("Normalized audio file is empty.")

        sha256 = self._hash_file(
            output_path,
            NormalizedAudioValidationError,
            "Normalized audio could not be hashed.",
        )
        return size_bytes, sha256

    def _run_command(
        self,
        command: Sequence[str],
        not_available_error: type[AudioNormalizationError],
    ) -> CommandResult:
        try:
            return self._command_runner.run(
                command,
                self._settings.normalization_timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise not_available_error("Configured audio tool is not available.") from exc
        except subprocess.TimeoutExpired as exc:
            raise NormalizationTimeoutError(
                "Audio normalization command timed out."
            ) from exc

    def _write_metadata_atomically(
        self,
        metadata: NormalizationMetadata,
        metadata_path: Path,
    ) -> None:
        temp_path = metadata_path.with_name(f".{metadata_path.name}.{uuid.uuid4().hex}.tmp")
        payload = metadata.model_dump_json(indent=2) + "\n"
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as metadata_file:
                metadata_file.write(payload)
                metadata_file.flush()
                os.fsync(metadata_file.fileno())
            os.replace(temp_path, metadata_path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove temporary normalization metadata file")
            raise NormalizationMetadataWriteError(
                "Normalization metadata could not be written."
            ) from exc

    def _publish_artifacts(
        self,
        staged_output_path: Path,
        output_path: Path,
        staged_metadata_path: Path,
        metadata_path: Path,
    ) -> None:
        published_output = False
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.exists() or metadata_path.exists():
                raise NormalizationStateError(
                    "Meeting package contains an inconsistent normalization state."
                )

            os.replace(staged_output_path, output_path)
            published_output = True

            if metadata_path.exists():
                raise NormalizationStateError(
                    "Meeting package contains an inconsistent normalization state."
                )
            os.replace(staged_metadata_path, metadata_path)
        except NormalizationStateError:
            if published_output:
                self._remove_published_output(output_path)
            raise
        except OSError as exc:
            if published_output:
                self._remove_published_output(output_path)
            raise NormalizationPublicationError(
                "Normalization artifacts could not be published."
            ) from exc

    def _remove_published_output(self, output_path: Path) -> None:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to remove published output after metadata failure")

    def _rollback_staging(self, staging_dir: Path) -> None:
        try:
            if staging_dir.exists():
                logger.info("Rolling back staged normalization artifacts")
                shutil.rmtree(staging_dir)
            staging_root = staging_dir.parent
            if staging_root.exists() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            logger.exception("Rollback failed for staged normalization artifacts")

    def _resolve_package_relative_path(
        self,
        package_root: Path,
        relative_path: str,
        error_type: type[AudioNormalizationError],
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
        error_type: type[AudioNormalizationError],
    ) -> None:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts or "\\" in relative_path:
            raise error_type("Package-relative path is unsafe.")

    def _raw_source_relative_path(self, payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        source = payload.get("source")
        if not isinstance(source, dict):
            return None
        relative_path = source.get("relative_path")
        return relative_path if isinstance(relative_path, str) else None

    def _hash_file(
        self,
        path: Path,
        error_type: type[AudioNormalizationError],
        message: str,
    ) -> str:
        checksum = hashlib.sha256()
        try:
            with path.open("rb") as file:
                while chunk := file.read(HASH_BUFFER_SIZE):
                    checksum.update(chunk)
        except OSError as exc:
            raise error_type(message) from exc
        return checksum.hexdigest()

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _parse_positive_int(self, value: Any, message: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise NormalizedAudioValidationError(message) from exc
        if parsed <= 0:
            raise NormalizedAudioValidationError(message)
        return parsed

    def _parse_non_negative_float(self, value: Any, message: str) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise NormalizedAudioValidationError(message) from exc
        if parsed < 0:
            raise NormalizedAudioValidationError(message)
        return parsed

    def _stderr_summary(self, stderr: str) -> str:
        cleaned = " | ".join(line.strip() for line in stderr.splitlines() if line.strip())
        if not cleaned:
            return "no stderr output"
        sanitized = self._sanitize_paths(cleaned)
        if len(sanitized) <= STDERR_SUMMARY_LIMIT:
            return sanitized
        return sanitized[: STDERR_SUMMARY_LIMIT - 3].rstrip() + "..."

    def _sanitize_paths(self, value: str) -> str:
        sanitized = re.sub(r"[A-Za-z]:[\\/][^\s|\"']+", "<path>", value)
        return re.sub(r"/(?:[^/\s|\"']+/)+[^/\s|\"']+", "<path>", sanitized)

    def _sanitize_tool_version(self, value: str) -> str:
        sanitized = self._sanitize_paths(value)
        return " ".join(sanitized.split())[:200]

    def _tool_name(self, executable: str) -> str:
        return Path(executable).name or executable

    def _is_relative_to(self, child: Path, parent: Path) -> bool:
        try:
            child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except ValueError:
            return False
        return True
