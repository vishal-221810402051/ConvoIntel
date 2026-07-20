"""Tests for canonical diarized raw transcription service."""

from __future__ import annotations

import hashlib
import json
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import backend.app.services.transcription.service as service_module
from backend.app.config import Settings, get_settings
from backend.app.models.meeting import AudioIntakeResult
from backend.app.models.normalization import (
    CANONICAL_NORMALIZATION_PROFILE,
    NORMALIZATION_CODEC,
    NORMALIZATION_SAMPLE_FORMAT,
    NORMALIZATION_SAMPLE_RATE_HZ,
    NORMALIZED_AUDIO_RELATIVE_PATH,
    NormalizationInputMetadata,
    NormalizationMetadata,
    NormalizationOutputMetadata,
    NormalizationToolMetadata,
)
from backend.app.models.transcription import (
    DurationTranscriptionUsage,
    RawTranscript,
    TokenTranscriptionUsage,
    TranscriptionMetadata,
    render_raw_text,
)
from backend.app.services.audio.intake import AudioIntakeService
from backend.app.services.transcription.errors import (
    TranscriptionApiKeyMissingError,
    TranscriptionInputIntegrityError,
    TranscriptionInputNotFoundError,
    TranscriptionMetadataWriteError,
    TranscriptionProviderError,
    TranscriptionProviderResponseError,
    TranscriptionPublicationError,
    TranscriptionStateError,
)
from backend.app.services.transcription.provider import (
    TranscriptionProviderRequest,
    TranscriptionProviderResult,
    TranscriptionProviderSegment,
)
from backend.app.services.transcription.service import TranscriptionService

FIXED_INTAKE_AT = datetime(2026, 7, 20, 15, 30, 45, 123456, tzinfo=timezone.utc)
FIXED_NORMALIZED_AT = datetime(2026, 7, 20, 15, 45, 0, 654321, tzinfo=timezone.utc)
FIXED_TRANSCRIBED_AT = datetime(2026, 7, 20, 16, 0, 1, 111111, tzinfo=timezone.utc)
NORMALIZED_BYTES = b"canonical normalized wav bytes"
DEFAULT_USAGE = object()


class FakeProvider:
    def __init__(
        self,
        result: TranscriptionProviderResult | Any | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result or valid_provider_result()
        self.error = error
        self.requests: list[TranscriptionProviderRequest] = []

    def transcribe(
        self,
        request: TranscriptionProviderRequest,
    ) -> TranscriptionProviderResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {"data_dir": tmp_path / "data", "openai_api_key": "test-key"}
    values.update(overrides)
    return Settings(**values)


def fixed_intake_clock() -> datetime:
    return FIXED_INTAKE_AT


def fixed_transcription_clock() -> datetime:
    return FIXED_TRANSCRIBED_AT


def suffix_sequence(values: list[str]) -> Callable[[], str]:
    iterator: Iterator[str] = iter(values)
    return lambda: next(iterator)


def write_silent_wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(b"\x00\x00" * 8000)
    return path


def create_normalized_package(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
) -> tuple[Settings, AudioIntakeResult, NormalizationMetadata]:
    resolved_settings = settings or make_settings(tmp_path)
    source = write_silent_wav(tmp_path / "meeting.wav")
    intake = AudioIntakeService(
        resolved_settings,
        clock=fixed_intake_clock,
        suffix_factory=suffix_sequence(["a1b2c3d4"]),
    ).intake_audio(source)

    normalized_path = intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH
    normalized_path.parent.mkdir(parents=True)
    normalized_path.write_bytes(NORMALIZED_BYTES)
    metadata = NormalizationMetadata(
        meeting_id=intake.meeting_id,
        created_at_utc=FIXED_NORMALIZED_AT,
        profile=CANONICAL_NORMALIZATION_PROFILE,
        input=NormalizationInputMetadata(
            relative_path=intake.manifest.source.relative_path,
            size_bytes=intake.manifest.source.size_bytes,
            sha256=intake.manifest.source.sha256,
        ),
        output=NormalizationOutputMetadata(
            size_bytes=len(NORMALIZED_BYTES),
            sha256=hashlib.sha256(NORMALIZED_BYTES).hexdigest(),
            duration_seconds=3.2,
            codec=NORMALIZATION_CODEC,
            sample_rate_hz=NORMALIZATION_SAMPLE_RATE_HZ,
            channels=1,
            sample_format=NORMALIZATION_SAMPLE_FORMAT,
        ),
        tool=NormalizationToolMetadata(name="ffmpeg", version="ffmpeg test"),
    )
    normalization_path = intake.meeting_dir / "metadata" / "normalization.json"
    normalization_path.write_text(
        metadata.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return resolved_settings, intake, metadata


def valid_provider_result(
    *,
    usage: Any = DEFAULT_USAGE,
    segments: list[TranscriptionProviderSegment] | None = None,
    text: str = "Alice will send the project plan. Bob will review the budget.",
) -> TranscriptionProviderResult:
    return TranscriptionProviderResult(
        text=text,
        duration_seconds=3.2,
        segments=segments
        if segments is not None
        else [
            TranscriptionProviderSegment(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=1.4,
                speaker_label="A",
                text="Alice will send the project plan.",
            ),
            TranscriptionProviderSegment(
                segment_id="seg_002",
                start_seconds=1.4,
                end_seconds=3.2,
                speaker_label="B",
                text="Bob will review the budget.",
            ),
        ],
        usage=DurationTranscriptionUsage(seconds=4)
        if usage is DEFAULT_USAGE
        else usage,
    )


def transcript_paths(intake: AudioIntakeResult) -> tuple[Path, Path, Path]:
    return (
        intake.meeting_dir / "transcript" / "raw.json",
        intake.meeting_dir / "transcript" / "raw.txt",
        intake.meeting_dir / "metadata" / "transcription.json",
    )


def assert_no_staging(intake: AudioIntakeResult) -> None:
    assert not (intake.meeting_dir / ".staging").exists()


def mutate_normalization_metadata(
    intake: AudioIntakeResult,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    path = intake.meeting_dir / "metadata" / "normalization.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def make_service(settings: Settings, provider: FakeProvider) -> TranscriptionService:
    return TranscriptionService(
        settings,
        provider=provider,
        clock=fixed_transcription_clock,
    )


def test_successful_transcription_with_fake_provider_publishes_artifacts(
    tmp_path: Path,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    provider = FakeProvider()
    normalized_before = (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).read_bytes()

    result = make_service(settings, provider).transcribe_meeting(intake.meeting_id)

    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    assert result.reused_existing is False
    assert result.raw_json_path == raw_json_path.resolve(strict=False)
    assert result.raw_text_path == raw_text_path.resolve(strict=False)
    assert result.transcription_metadata_path == metadata_path.resolve(strict=False)
    assert raw_json_path.exists()
    assert raw_text_path.exists()
    assert metadata_path.exists()
    assert result.transcript.text.startswith("Alice will")
    assert [segment.speaker_label for segment in result.transcript.segments] == ["A", "B"]
    assert raw_text_path.read_text(encoding="utf-8") == render_raw_text(result.transcript)
    assert (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).read_bytes() == normalized_before
    assert_no_staging(intake)

    metadata = TranscriptionMetadata.model_validate_json(
        metadata_path.read_text(encoding="utf-8")
    )
    assert metadata == result.metadata
    assert metadata.provider.model == settings.transcription_model
    assert metadata.provider.response_format == "diarized_json"
    assert metadata.provider.chunking_strategy == "auto"
    assert metadata.input.relative_path == NORMALIZED_AUDIO_RELATIVE_PATH
    assert metadata.input.sha256 == hashlib.sha256(NORMALIZED_BYTES).hexdigest()
    assert metadata.input.normalization_profile == "convointel-stt-wav-v1"
    assert metadata.transcript.segment_count == 2
    assert metadata.transcript.speaker_labels == ["A", "B"]
    assert metadata.transcript.structured_sha256 == hashlib.sha256(
        raw_json_path.read_bytes()
    ).hexdigest()
    assert metadata.transcript.text_sha256 == hashlib.sha256(
        raw_text_path.read_bytes()
    ).hexdigest()
    assert str(tmp_path) not in metadata_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in raw_json_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("language", "expected"),
    [(None, None), ("en", "en")],
)
def test_provider_request_configuration_and_language_behavior(
    tmp_path: Path,
    language: str | None,
    expected: str | None,
) -> None:
    settings = make_settings(tmp_path, transcription_language=language)
    settings, intake, _ = create_normalized_package(tmp_path, settings=settings)
    provider = FakeProvider()

    make_service(settings, provider).transcribe_meeting(intake.meeting_id)

    request = provider.requests[0]
    assert request.audio_path == intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH
    assert request.model == "gpt-4o-transcribe-diarize"
    assert request.response_format == "diarized_json"
    assert request.chunking_strategy == "auto"
    assert request.language == expected


@pytest.mark.parametrize(
    ("mutation", "error_type"),
    [
        (lambda intake: (intake.meeting_dir / "metadata" / "normalization.json").unlink(), TranscriptionInputNotFoundError),
        (lambda intake: (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).unlink(), TranscriptionInputNotFoundError),
        (lambda intake: (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).write_bytes(b"changed"), TranscriptionInputIntegrityError),
    ],
)
def test_normalized_input_artifacts_are_required_and_verified(
    tmp_path: Path,
    mutation: Callable[[AudioIntakeResult], None],
    error_type: type[Exception],
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    provider = FakeProvider()
    mutation(intake)

    with pytest.raises(error_type):
        make_service(settings, provider).transcribe_meeting(intake.meeting_id)

    assert not provider.requests
    assert_no_staging(intake)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("meeting_id", "mtg_20260720T153045123456Z_deadbeef"),
        lambda payload: payload["profile"].__setitem__("profile_id", "legacy"),
        lambda payload: payload["output"].__setitem__("relative_path", "../escape.wav"),
        lambda payload: payload["input"].__setitem__("sha256", "0" * 64),
    ],
)
def test_normalization_metadata_semantic_mismatches_are_rejected(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    mutate_normalization_metadata(intake, mutate)

    with pytest.raises(TranscriptionInputIntegrityError):
        make_service(settings, FakeProvider()).transcribe_meeting(intake.meeting_id)


@pytest.mark.parametrize(
    "segments",
    [
        [
            TranscriptionProviderSegment(
                segment_id="seg_001",
                start_seconds=-0.1,
                end_seconds=1.0,
                speaker_label="A",
                text="bad",
            )
        ],
        [
            TranscriptionProviderSegment(
                segment_id="seg_001",
                start_seconds=2.0,
                end_seconds=1.0,
                speaker_label="A",
                text="bad",
            )
        ],
        [
            TranscriptionProviderSegment(
                segment_id="dup",
                start_seconds=0.0,
                end_seconds=1.0,
                speaker_label="A",
                text="first",
            ),
            TranscriptionProviderSegment(
                segment_id="dup",
                start_seconds=1.0,
                end_seconds=2.0,
                speaker_label="B",
                text="second",
            ),
        ],
        [
            TranscriptionProviderSegment(
                segment_id="seg_001",
                start_seconds=0.0,
                end_seconds=2.0,
                speaker_label="A",
                text="first",
            ),
            TranscriptionProviderSegment(
                segment_id="seg_002",
                start_seconds=1.0,
                end_seconds=3.0,
                speaker_label="B",
                text="overlap",
            ),
        ],
    ],
)
def test_invalid_provider_segments_are_rejected(
    tmp_path: Path,
    segments: list[TranscriptionProviderSegment],
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)

    with pytest.raises(TranscriptionProviderResponseError):
        make_service(
            settings,
            FakeProvider(valid_provider_result(segments=segments)),
        ).transcribe_meeting(intake.meeting_id)

    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    assert not raw_json_path.exists()
    assert not raw_text_path.exists()
    assert not metadata_path.exists()
    assert_no_staging(intake)


def test_empty_transcript_is_accepted_and_renders_newline(tmp_path: Path) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    empty = valid_provider_result(segments=[], text="")

    result = make_service(settings, FakeProvider(empty)).transcribe_meeting(
        intake.meeting_id
    )

    assert result.transcript.text == ""
    assert result.transcript.segments == []
    assert result.raw_text_path.read_text(encoding="utf-8") == "\n"
    assert result.metadata.transcript.segment_count == 0
    assert result.metadata.transcript.speaker_labels == []


@pytest.mark.parametrize(
    "usage",
    [
        DurationTranscriptionUsage(seconds=5),
        TokenTranscriptionUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        None,
    ],
)
def test_usage_metadata_variants_are_persisted(
    tmp_path: Path,
    usage: Any,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)

    result = make_service(
        settings,
        FakeProvider(valid_provider_result(usage=usage)),
    ).transcribe_meeting(intake.meeting_id)

    assert result.metadata.usage == usage


def test_provider_failure_cleans_staging_and_preserves_normalized_audio(
    tmp_path: Path,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    normalized_before = (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).read_bytes()

    with pytest.raises(TranscriptionProviderError):
        make_service(
            settings,
            FakeProvider(error=TranscriptionProviderError("forced")),
        ).transcribe_meeting(intake.meeting_id)

    assert (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).read_bytes() == normalized_before
    assert_no_staging(intake)


def test_raw_json_write_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    service = make_service(settings, FakeProvider())
    real_write = service._write_json_atomically

    def fail_raw_json(model: Any, path: Path, error_type: Any, message: str) -> None:
        if path.name == "raw.json":
            raise TranscriptionPublicationError("forced")
        real_write(model, path, error_type, message)

    monkeypatch.setattr(service, "_write_json_atomically", fail_raw_json)

    with pytest.raises(TranscriptionPublicationError):
        service.transcribe_meeting(intake.meeting_id)

    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    assert not raw_json_path.exists()
    assert not raw_text_path.exists()
    assert not metadata_path.exists()
    assert_no_staging(intake)


def test_raw_text_write_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    service = make_service(settings, FakeProvider())
    real_write = service._write_text_atomically

    def fail_raw_text(payload: str, path: Path, error_type: Any, message: str) -> None:
        if path.name == "raw.txt":
            raise TranscriptionPublicationError("forced")
        real_write(payload, path, error_type, message)

    monkeypatch.setattr(service, "_write_text_atomically", fail_raw_text)

    with pytest.raises(TranscriptionPublicationError):
        service.transcribe_meeting(intake.meeting_id)

    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    assert not raw_json_path.exists()
    assert not raw_text_path.exists()
    assert not metadata_path.exists()
    assert_no_staging(intake)


def test_metadata_write_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    service = make_service(settings, FakeProvider())
    real_write = service._write_json_atomically

    def fail_metadata(model: Any, path: Path, error_type: Any, message: str) -> None:
        if path.name == "transcription.json":
            raise TranscriptionMetadataWriteError("forced")
        real_write(model, path, error_type, message)

    monkeypatch.setattr(service, "_write_json_atomically", fail_metadata)

    with pytest.raises(TranscriptionMetadataWriteError):
        service.transcribe_meeting(intake.meeting_id)

    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    assert not raw_json_path.exists()
    assert not raw_text_path.exists()
    assert not metadata_path.exists()
    assert_no_staging(intake)


def test_publication_failure_removes_published_transcript_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    real_replace = service_module.os.replace

    def fail_metadata_replace(source: Path | str, destination: Path | str) -> None:
        destination_path = Path(destination)
        if (
            destination_path.name == "transcription.json"
            and destination_path.parent.name == "metadata"
        ):
            raise OSError("forced")
        real_replace(source, destination)

    monkeypatch.setattr(service_module.os, "replace", fail_metadata_replace)

    with pytest.raises(TranscriptionPublicationError):
        make_service(settings, FakeProvider()).transcribe_meeting(intake.meeting_id)

    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    assert not raw_json_path.exists()
    assert not raw_text_path.exists()
    assert not metadata_path.exists()
    assert_no_staging(intake)


def test_valid_completed_transcription_is_reused_without_provider_call(
    tmp_path: Path,
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    first_provider = FakeProvider()
    service = make_service(settings, first_provider)
    first = service.transcribe_meeting(intake.meeting_id)
    second_provider = FakeProvider()

    second = make_service(settings, second_provider).transcribe_meeting(intake.meeting_id)

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.transcript == second.transcript
    assert first_provider.requests
    assert not second_provider.requests


@pytest.mark.parametrize("artifact", ["raw_json", "raw_text", "metadata"])
def test_partial_final_state_is_rejected(tmp_path: Path, artifact: str) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    if artifact == "raw_json":
        raw_json_path.parent.mkdir(parents=True)
        raw_json_path.write_text("{}\n", encoding="utf-8")
    elif artifact == "raw_text":
        raw_text_path.parent.mkdir(parents=True)
        raw_text_path.write_text("partial\n", encoding="utf-8")
    else:
        metadata_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(TranscriptionStateError):
        make_service(settings, FakeProvider()).transcribe_meeting(intake.meeting_id)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw_json, raw_text, metadata: raw_json.write_text("{}\n", encoding="utf-8"),
        lambda raw_json, raw_text, metadata: raw_text.write_text("changed\n", encoding="utf-8"),
        lambda raw_json, raw_text, metadata: metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(
                "gpt-4o-transcribe-diarize",
                "other-model",
            ),
            encoding="utf-8",
        ),
        lambda raw_json, raw_text, metadata: metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(
                '"segment_count": 2',
                '"segment_count": 99',
            ),
            encoding="utf-8",
        ),
    ],
)
def test_reused_transcription_mismatches_are_rejected(
    tmp_path: Path,
    mutation: Callable[[Path, Path, Path], None],
) -> None:
    settings, intake, _ = create_normalized_package(tmp_path)
    make_service(settings, FakeProvider()).transcribe_meeting(intake.meeting_id)
    raw_json_path, raw_text_path, metadata_path = transcript_paths(intake)
    mutation(raw_json_path, raw_text_path, metadata_path)

    with pytest.raises(TranscriptionStateError):
        make_service(settings, FakeProvider()).transcribe_meeting(intake.meeting_id)


def test_missing_api_key_raises_before_provider_request(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", openai_api_key=None)
    settings, intake, _ = create_normalized_package(tmp_path, settings=settings)

    with pytest.raises(TranscriptionApiKeyMissingError):
        TranscriptionService(settings, clock=fixed_transcription_clock).transcribe_meeting(
            intake.meeting_id
        )


def test_settings_secret_repr_and_environment_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("CONVOINTEL_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.setenv("CONVOINTEL_TRANSCRIPTION_LANGUAGE", "en")
    monkeypatch.setenv("CONVOINTEL_TRANSCRIPTION_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("CONVOINTEL_TRANSCRIPTION_MAX_RETRIES", "3")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-openai-key"
    assert "test-openai-key" not in repr(settings)
    assert "**********" in repr(settings)
    assert settings.transcription_language == "en"
    assert settings.transcription_timeout_seconds == 42
    assert settings.transcription_max_retries == 3


@pytest.mark.parametrize(
    "values",
    [
        {"transcription_model": "other"},
        {"transcription_language": "EN"},
        {"transcription_timeout_seconds": 0},
        {"transcription_max_retries": 99},
    ],
)
def test_transcription_settings_validation(values: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, **values)
