"""Tests for canonical audio normalization with mocked subprocess results."""

from __future__ import annotations

import hashlib
import json
import subprocess
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import backend.app.services.audio.normalization as normalization_module
from backend.app.config import Settings, get_settings
from backend.app.models.meeting import AudioIntakeResult, MeetingManifest
from backend.app.models.normalization import (
    CANONICAL_NORMALIZATION_PROFILE,
    NORMALIZATION_CHANNELS,
    NORMALIZATION_CODEC,
    NORMALIZATION_SAMPLE_FORMAT,
    NORMALIZATION_SAMPLE_RATE_HZ,
    NORMALIZED_AUDIO_RELATIVE_PATH,
    NormalizationMetadata,
)
from backend.app.services.audio.intake import AudioIntakeService
from backend.app.services.audio.normalization import (
    AudioNormalizationService,
    CommandResult,
)
from backend.app.services.audio.normalization_errors import (
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

FIXED_INTAKE_AT = datetime(2026, 7, 20, 15, 30, 45, 123456, tzinfo=timezone.utc)
FIXED_NORMALIZATION_AT = datetime(2026, 7, 20, 15, 45, 0, 654321, tzinfo=timezone.utc)
DEFAULT_OUTPUT_BYTES = b"mocked normalized pcm wav bytes"
VALID_MEETING_ID = "mtg_20260720T153045123456Z_a1b2c3d4"


class FakeCommandRunner:
    """Predictable FFmpeg/FFprobe runner for unit tests."""

    def __init__(
        self,
        *,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        output_bytes: bytes = DEFAULT_OUTPUT_BYTES,
        ffmpeg_returncode: int = 0,
        ffmpeg_stderr: str = "",
        ffprobe_returncode: int = 0,
        ffprobe_stdout: str | None = None,
        ffprobe_stderr: str = "",
        probe_payload: dict[str, Any] | None = None,
        missing_tool: str | None = None,
        timeout_tool: str | None = None,
    ) -> None:
        self.ffmpeg_binary = ffmpeg_binary
        self.ffprobe_binary = ffprobe_binary
        self.output_bytes = output_bytes
        self.ffmpeg_returncode = ffmpeg_returncode
        self.ffmpeg_stderr = ffmpeg_stderr
        self.ffprobe_returncode = ffprobe_returncode
        self.ffprobe_stdout = ffprobe_stdout
        self.ffprobe_stderr = ffprobe_stderr
        self.probe_payload = probe_payload
        self.missing_tool = missing_tool
        self.timeout_tool = timeout_tool
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[int] = []
        self.ffmpeg_calls: list[tuple[str, ...]] = []
        self.ffprobe_calls: list[tuple[str, ...]] = []
        self.version_calls: list[tuple[str, ...]] = []

    def run(self, args: Any, timeout_seconds: int) -> CommandResult:
        command = tuple(str(arg) for arg in args)
        self.calls.append(command)
        self.timeouts.append(timeout_seconds)

        if command[0] == self.ffmpeg_binary:
            if self.missing_tool == "ffmpeg":
                raise FileNotFoundError(command[0])
            if self.timeout_tool == "ffmpeg":
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            if command[1:] == ("-version",):
                self.version_calls.append(command)
                return CommandResult(
                    args=command,
                    returncode=0,
                    stdout="ffmpeg version 8.1.2-full_build-www.gyan.dev\nconfiguration: hidden",
                    stderr="",
                )

            self.ffmpeg_calls.append(command)
            if self.ffmpeg_returncode == 0:
                Path(command[-1]).write_bytes(self.output_bytes)
            return CommandResult(
                args=command,
                returncode=self.ffmpeg_returncode,
                stdout="",
                stderr=self.ffmpeg_stderr,
            )

        if command[0] == self.ffprobe_binary:
            if self.missing_tool == "ffprobe":
                raise FileNotFoundError(command[0])
            if self.timeout_tool == "ffprobe":
                raise subprocess.TimeoutExpired(command, timeout_seconds)

            self.ffprobe_calls.append(command)
            if self.ffprobe_stdout is not None:
                stdout = self.ffprobe_stdout
            else:
                output_path = Path(command[-1])
                payload = self.probe_payload or valid_probe_payload(output_path)
                stdout = json.dumps(payload)
            return CommandResult(
                args=command,
                returncode=self.ffprobe_returncode,
                stdout=stdout,
                stderr=self.ffprobe_stderr,
            )

        raise AssertionError(f"Unexpected command: {command}")


def make_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def fixed_intake_clock() -> datetime:
    return FIXED_INTAKE_AT


def fixed_normalization_clock() -> datetime:
    return FIXED_NORMALIZATION_AT


def suffix_sequence(values: list[str]) -> Callable[[], str]:
    iterator: Iterator[str] = iter(values)
    return lambda: next(iterator)


def make_intake_service(tmp_path: Path, settings: Settings) -> AudioIntakeService:
    return AudioIntakeService(
        settings,
        clock=fixed_intake_clock,
        suffix_factory=suffix_sequence(["a1b2c3d4"]),
    )


def make_normalization_service(
    settings: Settings,
    runner: FakeCommandRunner,
) -> AudioNormalizationService:
    return AudioNormalizationService(
        settings,
        command_runner=runner,
        clock=fixed_normalization_clock,
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


def create_meeting_package(
    tmp_path: Path,
    *,
    filename: str = "meeting.wav",
    writer: Callable[[Path], Path] = write_silent_wav,
) -> tuple[Settings, AudioIntakeResult]:
    settings = make_settings(tmp_path)
    source = writer(tmp_path / filename)
    result = make_intake_service(tmp_path, settings).intake_audio(source)
    return settings, result


def valid_probe_payload(output_path: Path) -> dict[str, Any]:
    size = output_path.stat().st_size if output_path.exists() else len(DEFAULT_OUTPUT_BYTES)
    return {
        "streams": [
            {
                "index": 0,
                "codec_name": NORMALIZATION_CODEC,
                "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                "channels": NORMALIZATION_CHANNELS,
            }
        ],
        "format": {
            "format_name": "wav",
            "duration": "1.000000",
            "size": str(size),
        },
    }


def metadata_path(result: AudioIntakeResult) -> Path:
    return result.meeting_dir / "metadata" / "normalization.json"


def output_path(result: AudioIntakeResult) -> Path:
    return result.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH


def staging_root(result: AudioIntakeResult) -> Path:
    return result.meeting_dir / ".staging"


def assert_no_staging_artifacts(result: AudioIntakeResult) -> None:
    assert not staging_root(result).exists()


def read_meeting_manifest(result: AudioIntakeResult) -> dict[str, Any]:
    return json.loads(result.metadata_path.read_text(encoding="utf-8"))


def write_meeting_manifest(result: AudioIntakeResult, payload: dict[str, Any]) -> None:
    result.metadata_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_mocked_ffprobe_valid_wav_package_normalizes_successfully(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    original_source_bytes = intake_result.source_audio_path.read_bytes()
    runner = FakeCommandRunner()

    result = make_normalization_service(settings, runner).normalize_meeting(
        intake_result.meeting_id,
    )

    assert result.reused_existing is False
    assert result.meeting_id == intake_result.meeting_id
    assert result.normalized_audio_path == output_path(intake_result).resolve(strict=False)
    assert result.normalization_metadata_path == metadata_path(intake_result).resolve(
        strict=False,
    )
    assert output_path(intake_result).read_bytes() == DEFAULT_OUTPUT_BYTES
    assert intake_result.source_audio_path.read_bytes() == original_source_bytes
    assert_no_staging_artifacts(intake_result)

    metadata = NormalizationMetadata.model_validate_json(
        metadata_path(intake_result).read_text(encoding="utf-8")
    )
    metadata_text = metadata_path(intake_result).read_text(encoding="utf-8")
    assert str(tmp_path) not in metadata_text
    assert metadata.meeting_id == intake_result.meeting_id
    assert metadata.created_at_utc == FIXED_NORMALIZATION_AT
    assert metadata.status == "normalization_completed"
    assert metadata.profile == CANONICAL_NORMALIZATION_PROFILE
    assert metadata.input.relative_path == intake_result.manifest.source.relative_path
    assert metadata.input.size_bytes == intake_result.manifest.source.size_bytes
    assert metadata.input.sha256 == intake_result.manifest.source.sha256
    assert metadata.output.relative_path == NORMALIZED_AUDIO_RELATIVE_PATH
    assert metadata.output.media_type == "audio/wav"
    assert metadata.output.size_bytes == len(DEFAULT_OUTPUT_BYTES)
    assert metadata.output.sha256 == hashlib.sha256(DEFAULT_OUTPUT_BYTES).hexdigest()
    assert metadata.output.duration_seconds == 1.0
    assert metadata.output.codec == NORMALIZATION_CODEC
    assert metadata.output.sample_rate_hz == NORMALIZATION_SAMPLE_RATE_HZ
    assert metadata.output.channels == NORMALIZATION_CHANNELS
    assert metadata.output.sample_format == NORMALIZATION_SAMPLE_FORMAT
    assert metadata.tool.name == "ffmpeg"
    assert metadata.tool.version == "ffmpeg version 8.1.2-full_build-www.gyan.dev"
    assert result.metadata == metadata


@pytest.mark.parametrize(
    ("filename", "expected_source"),
    [
        ("meeting.m4a", "source/original.m4a"),
        ("meeting.mp3", "source/original.mp3"),
    ],
)
def test_mocked_ffprobe_m4a_and_mp3_packages_use_source_manifest_path(
    tmp_path: Path,
    filename: str,
    expected_source: str,
) -> None:
    settings, intake_result = create_meeting_package(
        tmp_path,
        filename=filename,
        writer=write_opaque_audio,
    )
    runner = FakeCommandRunner()

    make_normalization_service(settings, runner).normalize_meeting(intake_result.meeting_id)

    assert intake_result.manifest.source.relative_path == expected_source
    assert runner.ffmpeg_calls[0][runner.ffmpeg_calls[0].index("-i") + 1].endswith(
        expected_source.replace("/", "\\")
    ) or runner.ffmpeg_calls[0][runner.ffmpeg_calls[0].index("-i") + 1].endswith(
        expected_source
    )


def test_mocked_ffprobe_exact_canonical_ffmpeg_arguments(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    runner = FakeCommandRunner()

    make_normalization_service(settings, runner).normalize_meeting(intake_result.meeting_id)

    ffmpeg_args = runner.ffmpeg_calls[0]
    staged_output = Path(ffmpeg_args[-1])
    assert ffmpeg_args == (
        settings.ffmpeg_binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(intake_result.source_audio_path),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "-1",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(staged_output),
    )
    assert staged_output.name == "audio.wav"
    assert staged_output.parent.name.startswith("normalization_")
    assert staged_output.parent.parent == intake_result.meeting_dir / ".staging"


def test_mocked_ffprobe_exact_ffprobe_arguments(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    runner = FakeCommandRunner()

    make_normalization_service(settings, runner).normalize_meeting(intake_result.meeting_id)

    ffprobe_args = runner.ffprobe_calls[0]
    assert ffprobe_args[:-1] == (
        settings.ffprobe_binary,
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
    )
    assert Path(ffprobe_args[-1]).name == "audio.wav"


def test_missing_meeting_package_raises_typed_error(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    with pytest.raises(MeetingPackageNotFoundError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            VALID_MEETING_ID,
        )


def test_missing_meeting_manifest_raises_typed_error(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    (settings.meetings_dir / VALID_MEETING_ID / "metadata").mkdir(parents=True)

    with pytest.raises(MeetingManifestNotFoundError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            VALID_MEETING_ID,
        )


def test_malformed_meeting_manifest_raises_typed_error(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manifest_path = settings.meetings_dir / VALID_MEETING_ID / "metadata" / "meeting.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{", encoding="utf-8")

    with pytest.raises(MeetingManifestInvalidError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            VALID_MEETING_ID,
        )


def test_requested_meeting_id_must_match_manifest(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    manifest = read_meeting_manifest(intake_result)
    manifest["meeting_id"] = "mtg_20260720T153045123456Z_deadbeef"
    write_meeting_manifest(intake_result, manifest)

    with pytest.raises(MeetingManifestInvalidError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            intake_result.meeting_id,
        )


def test_unsafe_source_relative_path_is_rejected(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    manifest = read_meeting_manifest(intake_result)
    manifest["source"]["relative_path"] = "../escape.wav"
    write_meeting_manifest(intake_result, manifest)

    with pytest.raises(SourceAudioIntegrityError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            intake_result.meeting_id,
        )


def test_source_audio_missing_raises_typed_error(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    intake_result.source_audio_path.unlink()

    with pytest.raises(SourceAudioMissingError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            intake_result.meeting_id,
        )


def test_source_size_mismatch_raises_integrity_error(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    intake_result.source_audio_path.write_bytes(
        intake_result.source_audio_path.read_bytes() + b"x"
    )

    with pytest.raises(SourceAudioIntegrityError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            intake_result.meeting_id,
        )


def test_source_checksum_mismatch_raises_integrity_error(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    intake_result.source_audio_path.write_bytes(
        b"z" * intake_result.manifest.source.size_bytes
    )

    with pytest.raises(SourceAudioIntegrityError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            intake_result.meeting_id,
        )


def test_ffmpeg_executable_missing_raises_typed_error_and_cleans_staging(
    tmp_path: Path,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    original_source_bytes = intake_result.source_audio_path.read_bytes()

    with pytest.raises(FfmpegNotAvailableError):
        make_normalization_service(
            settings,
            FakeCommandRunner(missing_tool="ffmpeg"),
        ).normalize_meeting(intake_result.meeting_id)

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert intake_result.source_audio_path.read_bytes() == original_source_bytes
    assert_no_staging_artifacts(intake_result)


def test_ffprobe_executable_missing_raises_typed_error_and_cleans_staging(
    tmp_path: Path,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)

    with pytest.raises(FfprobeNotAvailableError):
        make_normalization_service(
            settings,
            FakeCommandRunner(missing_tool="ffprobe"),
        ).normalize_meeting(intake_result.meeting_id)

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert_no_staging_artifacts(intake_result)


def test_ffmpeg_timeout_raises_distinct_error(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)

    with pytest.raises(NormalizationTimeoutError):
        make_normalization_service(
            settings,
            FakeCommandRunner(timeout_tool="ffmpeg"),
        ).normalize_meeting(intake_result.meeting_id)

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert_no_staging_artifacts(intake_result)


def test_ffmpeg_nonzero_exit_raises_process_error_and_sanitizes_stderr(
    tmp_path: Path,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    source_before = intake_result.source_audio_path.read_bytes()

    with pytest.raises(NormalizationProcessError) as exc_info:
        make_normalization_service(
            settings,
            FakeCommandRunner(
                ffmpeg_returncode=1,
                ffmpeg_stderr=f"bad input {intake_result.source_audio_path}",
            ),
        ).normalize_meeting(intake_result.meeting_id)

    assert str(tmp_path) not in str(exc_info.value)
    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert intake_result.source_audio_path.read_bytes() == source_before
    assert_no_staging_artifacts(intake_result)


def test_ffprobe_nonzero_exit_raises_validation_error(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)

    with pytest.raises(NormalizedAudioValidationError):
        make_normalization_service(
            settings,
            FakeCommandRunner(ffprobe_returncode=1, ffprobe_stderr="probe failed"),
        ).normalize_meeting(intake_result.meeting_id)

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert_no_staging_artifacts(intake_result)


def test_ffprobe_malformed_json_raises_validation_error(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)

    with pytest.raises(NormalizedAudioValidationError):
        make_normalization_service(
            settings,
            FakeCommandRunner(ffprobe_stdout="{"),
        ).normalize_meeting(intake_result.meeting_id)

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()


@pytest.mark.parametrize(
    ("probe_payload", "output_bytes"),
    [
        (
            {
                "streams": [],
                "format": {"format_name": "wav", "duration": "1.0", "size": "1"},
            },
            DEFAULT_OUTPUT_BYTES,
        ),
        (
            {
                "streams": [
                    {
                        "index": 0,
                        "codec_name": NORMALIZATION_CODEC,
                        "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                        "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                        "channels": NORMALIZATION_CHANNELS,
                    },
                    {
                        "index": 1,
                        "codec_name": NORMALIZATION_CODEC,
                        "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                        "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                        "channels": NORMALIZATION_CHANNELS,
                    },
                ],
                "format": {"format_name": "wav", "duration": "1.0", "size": "1"},
            },
            DEFAULT_OUTPUT_BYTES,
        ),
        (
            {
                "streams": [
                    {
                        "index": 0,
                        "codec_name": "aac",
                        "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                        "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                        "channels": NORMALIZATION_CHANNELS,
                    }
                ],
                "format": {"format_name": "wav", "duration": "1.0", "size": "1"},
            },
            DEFAULT_OUTPUT_BYTES,
        ),
        (
            {
                "streams": [
                    {
                        "index": 0,
                        "codec_name": NORMALIZATION_CODEC,
                        "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                        "sample_rate": "48000",
                        "channels": NORMALIZATION_CHANNELS,
                    }
                ],
                "format": {"format_name": "wav", "duration": "1.0", "size": "1"},
            },
            DEFAULT_OUTPUT_BYTES,
        ),
        (
            {
                "streams": [
                    {
                        "index": 0,
                        "codec_name": NORMALIZATION_CODEC,
                        "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                        "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                        "channels": 2,
                    }
                ],
                "format": {"format_name": "wav", "duration": "1.0", "size": "1"},
            },
            DEFAULT_OUTPUT_BYTES,
        ),
        (
            {
                "streams": [
                    {
                        "index": 0,
                        "codec_name": NORMALIZATION_CODEC,
                        "sample_fmt": "flt",
                        "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                        "channels": NORMALIZATION_CHANNELS,
                    }
                ],
                "format": {"format_name": "wav", "duration": "1.0", "size": "1"},
            },
            DEFAULT_OUTPUT_BYTES,
        ),
        (
            {
                "streams": [
                    {
                        "index": 0,
                        "codec_name": NORMALIZATION_CODEC,
                        "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                        "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                        "channels": NORMALIZATION_CHANNELS,
                    }
                ],
                "format": {"format_name": "wav", "duration": "1.0", "size": "0"},
            },
            b"",
        ),
    ],
)
def test_mocked_ffprobe_rejects_invalid_normalized_output(
    tmp_path: Path,
    probe_payload: dict[str, Any],
    output_bytes: bytes,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)

    with pytest.raises(NormalizedAudioValidationError):
        make_normalization_service(
            settings,
            FakeCommandRunner(probe_payload=probe_payload, output_bytes=output_bytes),
        ).normalize_meeting(intake_result.meeting_id)

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert_no_staging_artifacts(intake_result)


def test_metadata_write_failure_rolls_back_without_final_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    service = make_normalization_service(settings, FakeCommandRunner())
    original_source_bytes = intake_result.source_audio_path.read_bytes()

    def fail_write(metadata: NormalizationMetadata, target: Path) -> None:
        raise NormalizationMetadataWriteError("forced metadata failure")

    monkeypatch.setattr(service, "_write_metadata_atomically", fail_write)

    with pytest.raises(NormalizationMetadataWriteError):
        service.normalize_meeting(intake_result.meeting_id)

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert intake_result.source_audio_path.read_bytes() == original_source_bytes
    assert_no_staging_artifacts(intake_result)


def test_metadata_publication_failure_removes_newly_published_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    real_replace = normalization_module.os.replace

    def fail_final_metadata_replace(source: Path | str, destination: Path | str) -> None:
        destination_path = Path(destination)
        if (
            destination_path.name == "normalization.json"
            and destination_path.parent.name == "metadata"
        ):
            raise OSError("forced publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(normalization_module.os, "replace", fail_final_metadata_replace)

    with pytest.raises(NormalizationPublicationError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            intake_result.meeting_id,
        )

    assert not output_path(intake_result).exists()
    assert not metadata_path(intake_result).exists()
    assert_no_staging_artifacts(intake_result)


@pytest.mark.parametrize("artifact", ["output", "metadata"])
def test_partial_final_normalization_state_raises_state_error(
    tmp_path: Path,
    artifact: str,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    if artifact == "output":
        output_path(intake_result).parent.mkdir(parents=True)
        output_path(intake_result).write_bytes(DEFAULT_OUTPUT_BYTES)
    else:
        metadata_path(intake_result).parent.mkdir(parents=True, exist_ok=True)
        metadata_path(intake_result).write_text("{}\n", encoding="utf-8")

    with pytest.raises(NormalizationStateError):
        make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
            intake_result.meeting_id,
        )


def test_valid_completed_normalization_is_reused_without_rerunning_ffmpeg(
    tmp_path: Path,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    runner = FakeCommandRunner()
    service = make_normalization_service(settings, runner)

    first = service.normalize_meeting(intake_result.meeting_id)
    second = service.normalize_meeting(intake_result.meeting_id)

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.metadata == second.metadata
    assert len(runner.ffmpeg_calls) == 1
    assert len(runner.version_calls) == 1
    assert len(runner.ffprobe_calls) == 2


def test_reused_output_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    service = make_normalization_service(settings, FakeCommandRunner())
    service.normalize_meeting(intake_result.meeting_id)
    output_path(intake_result).write_bytes(b"x" * len(DEFAULT_OUTPUT_BYTES))

    with pytest.raises(NormalizationStateError):
        service.normalize_meeting(intake_result.meeting_id)


def test_reused_output_profile_mismatch_is_rejected(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    service = make_normalization_service(settings, FakeCommandRunner())
    service.normalize_meeting(intake_result.meeting_id)
    payload = json.loads(metadata_path(intake_result).read_text(encoding="utf-8"))
    payload["profile"]["profile_id"] = "legacy-profile"
    metadata_path(intake_result).write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(NormalizationStateError):
        service.normalize_meeting(intake_result.meeting_id)


def test_reused_output_probe_mismatch_is_rejected_as_state_error(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    runner = FakeCommandRunner()
    service = make_normalization_service(settings, runner)
    service.normalize_meeting(intake_result.meeting_id)
    runner.probe_payload = {
        "streams": [
            {
                "index": 0,
                "codec_name": "aac",
                "sample_fmt": NORMALIZATION_SAMPLE_FORMAT,
                "sample_rate": str(NORMALIZATION_SAMPLE_RATE_HZ),
                "channels": NORMALIZATION_CHANNELS,
            }
        ],
        "format": {"format_name": "wav", "duration": "1.0", "size": "1"},
    }

    with pytest.raises(NormalizationStateError):
        service.normalize_meeting(intake_result.meeting_id)


def test_settings_overrides_for_binaries_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONVOINTEL_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.setenv("CONVOINTEL_FFMPEG_BINARY", "custom-ffmpeg.exe")
    monkeypatch.setenv("CONVOINTEL_FFPROBE_BINARY", "custom-ffprobe.exe")
    monkeypatch.setenv("CONVOINTEL_NORMALIZATION_TIMEOUT_SECONDS", "42")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ffmpeg_binary == "custom-ffmpeg.exe"
    assert settings.ffprobe_binary == "custom-ffprobe.exe"
    assert settings.normalization_timeout_seconds == 42
    assert settings.data_dir == (tmp_path / "runtime-data").resolve(strict=False)


def test_command_runner_receives_configured_timeout(tmp_path: Path) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    settings = Settings(
        data_dir=settings.data_dir,
        normalization_timeout_seconds=42,
    )
    runner = FakeCommandRunner()

    make_normalization_service(settings, runner).normalize_meeting(intake_result.meeting_id)

    assert runner.timeouts
    assert set(runner.timeouts) == {42}


def test_manifest_written_by_intake_still_validates_after_normalization(
    tmp_path: Path,
) -> None:
    settings, intake_result = create_meeting_package(tmp_path)
    manifest_before = MeetingManifest.model_validate_json(
        intake_result.metadata_path.read_text(encoding="utf-8")
    )

    make_normalization_service(settings, FakeCommandRunner()).normalize_meeting(
        intake_result.meeting_id,
    )

    manifest_after = MeetingManifest.model_validate_json(
        intake_result.metadata_path.read_text(encoding="utf-8")
    )
    assert manifest_after == manifest_before
