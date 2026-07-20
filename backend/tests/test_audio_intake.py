"""Tests for canonical local audio intake."""

from __future__ import annotations

import hashlib
import json
import re
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.models.meeting import MeetingManifest
from backend.app.services.audio.errors import (
    EmptyAudioFileError,
    MeetingIdCollisionError,
    MeetingPackageWriteError,
    SourceAudioNotFileError,
    SourceAudioNotFoundError,
    UnsupportedAudioFormatError,
)
from backend.app.services.audio.intake import AudioIntakeService

FIXED_CREATED_AT = datetime(2026, 7, 20, 15, 30, 45, 123456, tzinfo=timezone.utc)
MEETING_ID_PATTERN = re.compile(r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")


def make_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def fixed_clock() -> datetime:
    return FIXED_CREATED_AT


def suffix_sequence(values: list[str]) -> Callable[[], str]:
    iterator: Iterator[str] = iter(values)
    return lambda: next(iterator)


def make_service(
    tmp_path: Path,
    *,
    suffixes: list[str] | None = None,
    max_meeting_id_attempts: int = 10,
) -> AudioIntakeService:
    return AudioIntakeService(
        make_settings(tmp_path),
        clock=fixed_clock,
        suffix_factory=suffix_sequence(suffixes or ["a1b2c3d4"]),
        max_meeting_id_attempts=max_meeting_id_attempts,
    )


def write_opaque_audio(path: Path, payload: bytes = b"opaque audio bytes") -> Path:
    path.write_bytes(payload)
    return path


def write_silent_wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(b"\x00\x00" * 8000)
    return path


def read_manifest(path: Path) -> MeetingManifest:
    return MeetingManifest.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("filename", "writer", "media_type"),
    [
        ("meeting.m4a", write_opaque_audio, "audio/mp4"),
        ("meeting.mp3", write_opaque_audio, "audio/mpeg"),
        ("meeting.wav", write_silent_wav, "audio/wav"),
    ],
)
def test_valid_audio_intake_creates_canonical_package(
    tmp_path: Path,
    filename: str,
    writer: Callable[[Path], Path],
    media_type: str,
) -> None:
    source = writer(tmp_path / filename)
    result = make_service(tmp_path).intake_audio(source)

    assert MEETING_ID_PATTERN.fullmatch(result.meeting_id)
    assert result.meeting_dir.exists()
    assert result.source_audio_path.exists()
    assert result.metadata_path.exists()
    assert result.source_audio_path.name == f"original{source.suffix.lower()}"
    assert result.source_audio_path.read_bytes() == source.read_bytes()
    assert result.manifest.source.media_type == media_type
    assert result.manifest.source.size_bytes == source.stat().st_size
    assert result.manifest.source.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result.manifest.status == "intake_completed"
    assert result.manifest.source.relative_path == f"source/original{source.suffix.lower()}"
    assert not Path(result.manifest.source.relative_path).is_absolute()
    assert str(source.resolve()) not in result.metadata_path.read_text(encoding="utf-8")
    assert read_manifest(result.metadata_path) == result.manifest


def test_uppercase_extension_is_accepted(tmp_path: Path) -> None:
    source = write_opaque_audio(tmp_path / "VOICE.MP3")

    result = make_service(tmp_path).intake_audio(source)

    assert result.manifest.source.extension == ".mp3"
    assert result.manifest.source.stored_filename == "original.mp3"
    assert result.manifest.source.media_type == "audio/mpeg"


def test_missing_source_file_is_rejected_before_package_creation(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    service = AudioIntakeService(
        settings,
        clock=fixed_clock,
        suffix_factory=suffix_sequence(["a1b2c3d4"]),
    )

    with pytest.raises(SourceAudioNotFoundError):
        service.intake_audio(tmp_path / "missing.wav")

    assert not settings.meetings_dir.exists()


def test_source_directory_is_rejected(tmp_path: Path) -> None:
    source_dir = tmp_path / "folder.wav"
    source_dir.mkdir()

    with pytest.raises(SourceAudioNotFileError):
        make_service(tmp_path).intake_audio(source_dir)


def test_unsupported_extension_is_rejected(tmp_path: Path) -> None:
    source = write_opaque_audio(tmp_path / "meeting.flac")

    with pytest.raises(UnsupportedAudioFormatError):
        make_service(tmp_path).intake_audio(source)


def test_zero_byte_source_file_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "empty.wav"
    source.write_bytes(b"")

    with pytest.raises(EmptyAudioFileError):
        make_service(tmp_path).intake_audio(source)


def test_same_input_can_create_two_distinct_meetings(tmp_path: Path) -> None:
    source = write_opaque_audio(tmp_path / "meeting.m4a")
    service = make_service(tmp_path, suffixes=["11111111", "22222222"])

    first = service.intake_audio(source)
    second = service.intake_audio(source)

    assert first.meeting_id != second.meeting_id
    assert first.meeting_dir != second.meeting_dir
    assert first.source_audio_path.read_bytes() == second.source_audio_path.read_bytes()


def test_manifest_contains_only_package_relative_paths(tmp_path: Path) -> None:
    source = write_opaque_audio(tmp_path / "meeting.mp3")

    result = make_service(tmp_path).intake_audio(source)
    manifest_data = json.loads(result.metadata_path.read_text(encoding="utf-8"))

    assert manifest_data["source"]["relative_path"] == "source/original.mp3"
    assert "source_path" not in manifest_data
    assert "stored_path" not in manifest_data
    assert str(result.source_audio_path) not in result.metadata_path.read_text(encoding="utf-8")


def test_custom_temporary_data_root_is_used(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "custom-data")
    source = write_opaque_audio(tmp_path / "meeting.m4a")
    service = AudioIntakeService(
        settings,
        clock=fixed_clock,
        suffix_factory=suffix_sequence(["a1b2c3d4"]),
    )

    result = service.intake_audio(source)

    assert result.meeting_dir.parent == settings.meetings_dir
    assert result.meeting_dir.is_relative_to(settings.meetings_dir)


def test_final_meeting_directory_is_not_published_after_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    source = write_opaque_audio(tmp_path / "meeting.wav")
    service = AudioIntakeService(
        settings,
        clock=fixed_clock,
        suffix_factory=suffix_sequence(["deadbeef"]),
    )

    def fail_copy(source_path: Path, destination: Path) -> str:
        raise MeetingPackageWriteError("forced copy failure")

    monkeypatch.setattr(service, "_copy_source_audio", fail_copy)

    with pytest.raises(MeetingPackageWriteError):
        service.intake_audio(source)

    final_dir = settings.meetings_dir / "mtg_20260720T153045123456Z_deadbeef"
    assert not final_dir.exists()
    assert not any((settings.meetings_dir / ".staging").iterdir())


def test_staging_package_is_removed_after_manifest_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    source = write_opaque_audio(tmp_path / "meeting.m4a")
    service = AudioIntakeService(
        settings,
        clock=fixed_clock,
        suffix_factory=suffix_sequence(["deadbeef"]),
    )

    def fail_write(manifest: MeetingManifest, metadata_path: Path) -> None:
        raise MeetingPackageWriteError("forced manifest failure")

    monkeypatch.setattr(service, "_write_manifest_atomically", fail_write)

    with pytest.raises(MeetingPackageWriteError):
        service.intake_audio(source)

    final_dir = settings.meetings_dir / "mtg_20260720T153045123456Z_deadbeef"
    assert not final_dir.exists()
    assert not any((settings.meetings_dir / ".staging").iterdir())


def test_collision_retry_succeeds_when_later_id_is_available(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = write_opaque_audio(tmp_path / "meeting.mp3")
    collision_id = "mtg_20260720T153045123456Z_aaaaaaaa"
    (settings.meetings_dir / collision_id).mkdir(parents=True)
    service = AudioIntakeService(
        settings,
        clock=fixed_clock,
        suffix_factory=suffix_sequence(["aaaaaaaa", "bbbbbbbb"]),
    )

    result = service.intake_audio(source)

    assert result.meeting_id == "mtg_20260720T153045123456Z_bbbbbbbb"


def test_collision_exhaustion_raises_typed_error(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    source = write_opaque_audio(tmp_path / "meeting.wav")
    collision_id = "mtg_20260720T153045123456Z_aaaaaaaa"
    (settings.meetings_dir / collision_id).mkdir(parents=True)
    service = AudioIntakeService(
        settings,
        clock=fixed_clock,
        suffix_factory=suffix_sequence(["aaaaaaaa", "aaaaaaaa"]),
        max_meeting_id_attempts=2,
    )

    with pytest.raises(MeetingIdCollisionError):
        service.intake_audio(source)

    assert not any((settings.meetings_dir / ".staging").iterdir()) if (
        settings.meetings_dir / ".staging"
    ).exists() else True
