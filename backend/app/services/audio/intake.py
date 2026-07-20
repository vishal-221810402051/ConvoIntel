"""Local source-audio intake into canonical meeting packages."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from backend.app.config import Settings, get_settings
from backend.app.models.meeting import (
    AudioIntakeResult,
    MeetingManifest,
    SourceAudioMetadata,
)
from backend.app.services.audio.errors import (
    AudioIntakeError,
    EmptyAudioFileError,
    MeetingIdCollisionError,
    MeetingPackageWriteError,
    SourceAudioNotFileError,
    SourceAudioNotFoundError,
    SourceAudioReadError,
    UnsupportedAudioFormatError,
)

logger = logging.getLogger(__name__)

SUPPORTED_MEDIA_TYPES = MappingProxyType(
    {
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
    }
)
COPY_BUFFER_SIZE = 1024 * 1024
DEFAULT_MAX_MEETING_ID_ATTEMPTS = 10
_SUFFIX_PATTERN = re.compile(r"^[0-9a-f]{8,32}$")


Clock = Callable[[], datetime]
SuffixFactory = Callable[[], str]


class AudioIntakeService:
    """Preserve a local source-audio file as a canonical meeting package."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        clock: Clock | None = None,
        suffix_factory: SuffixFactory | None = None,
        max_meeting_id_attempts: int = DEFAULT_MAX_MEETING_ID_ATTEMPTS,
    ) -> None:
        if max_meeting_id_attempts < 1:
            raise ValueError("max_meeting_id_attempts must be at least 1")

        self._settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._suffix_factory = suffix_factory or (lambda: secrets.token_hex(4))
        self._max_meeting_id_attempts = max_meeting_id_attempts

    def intake_audio(self, source_path: str | Path) -> AudioIntakeResult:
        source = Path(source_path).expanduser()
        logger.info("Starting local audio intake for %s", source.name or "<unnamed>")

        source_info = self._validate_source(source)
        meetings_dir = self._settings.meetings_dir
        meetings_dir.mkdir(parents=True, exist_ok=True)

        meeting_id, created_at = self._reserve_meeting_id(meetings_dir)
        logger.info("Generated meeting ID %s", meeting_id)

        staging_root = meetings_dir / ".staging"
        staging_dir: Path | None = None

        try:
            staging_root.mkdir(parents=True, exist_ok=True)
            staging_dir = staging_root / f"{meeting_id}_{uuid.uuid4().hex}"
            staging_dir.mkdir(parents=False, exist_ok=False)

            source_dir = staging_dir / "source"
            metadata_dir = staging_dir / "metadata"
            source_dir.mkdir()
            metadata_dir.mkdir()

            extension = source_info["extension"]
            stored_filename = f"original{extension}"
            stored_audio_path = source_dir / stored_filename
            checksum = self._copy_source_audio(source, stored_audio_path)

            manifest = MeetingManifest(
                meeting_id=meeting_id,
                created_at_utc=created_at,
                source=SourceAudioMetadata(
                    original_filename=source.name,
                    stored_filename=stored_filename,
                    relative_path=f"source/{stored_filename}",
                    extension=extension,
                    media_type=SUPPORTED_MEDIA_TYPES[extension],
                    size_bytes=source_info["size_bytes"],
                    sha256=checksum,
                ),
            )

            metadata_path = metadata_dir / "meeting.json"
            self._write_manifest_atomically(manifest, metadata_path)

            final_meeting_dir = meetings_dir / meeting_id
            if final_meeting_dir.exists():
                raise MeetingPackageWriteError("Meeting package already exists.")

            staging_dir.rename(final_meeting_dir)
            staging_dir = None

            result = AudioIntakeResult(
                meeting_id=meeting_id,
                meeting_dir=final_meeting_dir.resolve(strict=False),
                source_audio_path=(final_meeting_dir / "source" / stored_filename).resolve(
                    strict=False,
                ),
                metadata_path=(final_meeting_dir / "metadata" / "meeting.json").resolve(
                    strict=False,
                ),
                manifest=manifest,
            )
            logger.info("Completed local audio intake for meeting %s", meeting_id)
            return result
        except Exception as exc:
            if staging_dir is not None:
                self._rollback_staging(staging_dir)
            if isinstance(exc, AudioIntakeError):
                raise
            raise MeetingPackageWriteError("Meeting package could not be written.") from exc

    def _validate_source(self, source: Path) -> dict[str, object]:
        try:
            if not source.exists():
                logger.info("Audio intake validation failed: source_not_found")
                raise SourceAudioNotFoundError("Source audio file was not found.")
            if not source.is_file():
                logger.info("Audio intake validation failed: source_not_file")
                raise SourceAudioNotFileError("Source audio path is not a regular file.")
        except OSError as exc:
            logger.info("Audio intake validation failed: source_read_error")
            raise SourceAudioReadError("Source audio could not be inspected.") from exc

        extension = source.suffix.lower()
        if extension not in SUPPORTED_MEDIA_TYPES:
            logger.info("Audio intake validation failed: unsupported_format")
            allowed = ", ".join(sorted(SUPPORTED_MEDIA_TYPES))
            raise UnsupportedAudioFormatError(
                f"Unsupported audio format. Allowed extensions: {allowed}."
            )

        try:
            size_bytes = source.stat().st_size
        except OSError as exc:
            logger.info("Audio intake validation failed: source_read_error")
            raise SourceAudioReadError("Source audio could not be inspected.") from exc

        if size_bytes == 0:
            logger.info("Audio intake validation failed: empty_file")
            raise EmptyAudioFileError("Source audio file is empty.")

        try:
            with source.open("rb") as source_file:
                source_file.read(0)
        except OSError as exc:
            logger.info("Audio intake validation failed: source_read_error")
            raise SourceAudioReadError("Source audio could not be opened for reading.") from exc

        return {"extension": extension, "size_bytes": size_bytes}

    def _reserve_meeting_id(self, meetings_dir: Path) -> tuple[str, datetime]:
        for _ in range(self._max_meeting_id_attempts):
            created_at = self._utc_now()
            suffix = self._suffix_factory().strip().lower()
            if not _SUFFIX_PATTERN.fullmatch(suffix):
                raise MeetingPackageWriteError(
                    "Meeting ID suffix source returned an invalid suffix."
                )

            meeting_id = f"mtg_{created_at.strftime('%Y%m%dT%H%M%S%fZ')}_{suffix}"
            if not (meetings_dir / meeting_id).exists():
                return meeting_id, created_at

            logger.debug("Meeting ID collision encountered for %s", meeting_id)

        logger.info("Audio intake validation failed: meeting_id_collision")
        raise MeetingIdCollisionError("Could not generate a unique meeting ID.")

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _copy_source_audio(self, source: Path, destination: Path) -> str:
        checksum = hashlib.sha256()
        try:
            source_file = source.open("rb")
        except OSError as exc:
            raise SourceAudioReadError("Source audio could not be opened for reading.") from exc

        try:
            destination_file = destination.open("wb")
        except OSError as exc:
            source_file.close()
            raise MeetingPackageWriteError("Source audio could not be preserved.") from exc

        try:
            with source_file, destination_file:
                while True:
                    try:
                        chunk = source_file.read(COPY_BUFFER_SIZE)
                    except OSError as exc:
                        raise SourceAudioReadError("Source audio could not be read.") from exc
                    if not chunk:
                        break
                    try:
                        destination_file.write(chunk)
                    except OSError as exc:
                        raise MeetingPackageWriteError(
                            "Source audio could not be preserved."
                        ) from exc
                    checksum.update(chunk)
                destination_file.flush()
                os.fsync(destination_file.fileno())
        except OSError as exc:
            raise MeetingPackageWriteError("Source audio could not be preserved.") from exc
        return checksum.hexdigest()

    def _write_manifest_atomically(
        self,
        manifest: MeetingManifest,
        metadata_path: Path,
    ) -> None:
        temp_path = metadata_path.with_name(f".{metadata_path.name}.{uuid.uuid4().hex}.tmp")
        payload = manifest.model_dump_json(indent=2) + "\n"
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
                logger.exception("Failed to remove temporary meeting metadata file")
            raise MeetingPackageWriteError("Meeting metadata could not be written.") from exc

    def _rollback_staging(self, staging_dir: Path) -> None:
        logger.info("Rolling back incomplete meeting package")
        try:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
        except OSError:
            logger.exception("Rollback failed for incomplete meeting package")
