"""Tests for canonical transcript cleanup service."""

from __future__ import annotations

import hashlib
import json
import shutil
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import backend.app.services.cleanup.service as service_module
from backend.app.config import CLEANUP_MODEL, Settings, get_settings
from backend.app.models.cleanup import (
    CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH,
    CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH,
    CLEANUP_METADATA_RELATIVE_PATH,
    CLEANUP_PROMPT_VERSION,
    CLEANUP_RESPONSE_FORMAT_NAME,
    CleanedTranscript,
    CleanupMetadata,
    CleanupUsage,
    render_cleaned_text,
)
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
    RawTranscript,
    TranscriptSegment,
    TranscriptionArtifactsMetadata,
    TranscriptionInputMetadata,
    TranscriptionMetadata,
    TranscriptionProviderMetadata,
    render_raw_text,
)
from backend.app.services.audio.intake import AudioIntakeService
from backend.app.services.cleanup.errors import (
    CleanupFidelityError,
    CleanupInputIntegrityError,
    CleanupInputNotFoundError,
    CleanupMetadataWriteError,
    CleanupProviderError,
    CleanupProviderResponseError,
    CleanupPublicationError,
    CleanupStateError,
)
from backend.app.services.cleanup.provider import (
    CleanupProviderRequest,
    CleanupProviderResult,
    CleanupProviderSegmentResult,
)
from backend.app.services.cleanup.service import TranscriptCleanupService

FIXED_INTAKE_AT = datetime(2026, 7, 20, 15, 30, 45, 123456, tzinfo=timezone.utc)
FIXED_NORMALIZED_AT = datetime(2026, 7, 20, 15, 45, 0, 654321, tzinfo=timezone.utc)
FIXED_TRANSCRIBED_AT = datetime(2026, 7, 20, 16, 0, 1, 111111, tzinfo=timezone.utc)
FIXED_CLEANED_AT = datetime(2026, 7, 20, 16, 20, 2, 222222, tzinfo=timezone.utc)
NORMALIZED_BYTES = b"canonical normalized wav bytes"


class FakeCleanupProvider:
    def __init__(
        self,
        *,
        cleaner: Callable[[str], str] | None = None,
        result_factory: Callable[[CleanupProviderRequest], CleanupProviderResult] | None = None,
        error: Exception | None = None,
        error_on_call: int = 1,
        usage: CleanupUsage | None = None,
    ) -> None:
        self.cleaner = cleaner or default_cleaner
        self.result_factory = result_factory
        self.error = error
        self.error_on_call = error_on_call
        self.usage = usage
        self.requests: list[CleanupProviderRequest] = []

    def clean_batch(self, request: CleanupProviderRequest) -> CleanupProviderResult:
        self.requests.append(request)
        if self.error is not None and len(self.requests) == self.error_on_call:
            raise self.error
        if self.result_factory is not None:
            return self.result_factory(request)
        return CleanupProviderResult(
            segments=[
                CleanupProviderSegmentResult(
                    segment_id=segment.segment_id,
                    cleaned_text=self.cleaner(segment.text),
                )
                for segment in request.segments
            ],
            usage=self.usage,
        )


def default_cleaner(value: str) -> str:
    cleaned = " ".join(value.split()).replace("every one", "everyone")
    if cleaned.startswith("um hello"):
        cleaned = "Hello" + cleaned.removeprefix("um hello")
    if cleaned and cleaned[-1] not in ".?!":
        cleaned += "."
    return cleaned


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {"data_dir": tmp_path / "data", "openai_api_key": "test-key"}
    values.update(overrides)
    return Settings(**values)


def fixed_intake_clock() -> datetime:
    return FIXED_INTAKE_AT


def fixed_cleanup_clock() -> datetime:
    return FIXED_CLEANED_AT


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


def default_raw_segments() -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            segment_id="seg_001",
            start_seconds=0.0,
            end_seconds=1.4,
            speaker_label="A",
            text="um hello every one the pilot starts 30/07/2026 at 14:30 and the budget is €2500",
        ),
        TranscriptSegment(
            segment_id="seg_002",
            start_seconds=1.4,
            end_seconds=3.0,
            speaker_label="B",
            text="alice will send v2.4 of plan ABC-123 to team@example.com",
        ),
        TranscriptSegment(
            segment_id="seg_003",
            start_seconds=3.0,
            end_seconds=5.5,
            speaker_label="A",
            text="bob will review https://example.com/plan and bonjour nous gardons cette partie en francais",
        ),
    ]


def create_phase4_package(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
    segments: list[TranscriptSegment] | None = None,
    duration_seconds: float = 5.5,
) -> tuple[Settings, AudioIntakeResult, RawTranscript, NormalizationMetadata, TranscriptionMetadata]:
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
    normalization = NormalizationMetadata(
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
            sha256=file_sha256(normalized_path),
            duration_seconds=duration_seconds,
            codec=NORMALIZATION_CODEC,
            sample_rate_hz=NORMALIZATION_SAMPLE_RATE_HZ,
            channels=1,
            sample_format=NORMALIZATION_SAMPLE_FORMAT,
        ),
        tool=NormalizationToolMetadata(name="ffmpeg", version="ffmpeg test"),
    )
    normalization_path = intake.meeting_dir / "metadata" / "normalization.json"
    write_model(normalization, normalization_path)

    raw_segments = segments if segments is not None else default_raw_segments()
    raw = RawTranscript(
        meeting_id=intake.meeting_id,
        text="\n".join(segment.text for segment in raw_segments),
        duration_seconds=duration_seconds,
        segments=raw_segments,
    )
    raw_json_path = intake.meeting_dir / "transcript" / "raw.json"
    raw_text_path = intake.meeting_dir / "transcript" / "raw.txt"
    raw_json_path.parent.mkdir(parents=True)
    write_model(raw, raw_json_path)
    raw_text_path.write_text(render_raw_text(raw), encoding="utf-8", newline="\n")

    transcription = TranscriptionMetadata(
        meeting_id=intake.meeting_id,
        created_at_utc=FIXED_TRANSCRIBED_AT,
        provider=TranscriptionProviderMetadata(),
        input=TranscriptionInputMetadata(
            size_bytes=len(NORMALIZED_BYTES),
            sha256=file_sha256(normalized_path),
            duration_seconds=duration_seconds,
        ),
        transcript=TranscriptionArtifactsMetadata(
            text_size_bytes=raw_text_path.stat().st_size,
            text_sha256=file_sha256(raw_text_path),
            structured_size_bytes=raw_json_path.stat().st_size,
            structured_sha256=file_sha256(raw_json_path),
            segment_count=len(raw.segments),
            speaker_labels=raw.speaker_labels,
        ),
        usage=None,
    )
    write_model(transcription, intake.meeting_dir / "metadata" / "transcription.json")
    return resolved_settings, intake, raw, normalization, transcription


def make_service(settings: Settings, provider: FakeCleanupProvider) -> TranscriptCleanupService:
    return TranscriptCleanupService(
        settings,
        provider=provider,
        clock=fixed_cleanup_clock,
    )


def cleanup_paths(intake: AudioIntakeResult) -> tuple[Path, Path, Path]:
    return (
        intake.meeting_dir / CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH,
        intake.meeting_dir / CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH,
        intake.meeting_dir / CLEANUP_METADATA_RELATIVE_PATH,
    )


def write_model(model: Any, path: Path) -> None:
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mutate_json(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def assert_no_staging(intake: AudioIntakeResult) -> None:
    assert not (intake.meeting_dir / ".staging").exists()


def assert_cleanup_artifacts_absent(intake: AudioIntakeResult) -> None:
    for path in cleanup_paths(intake):
        assert not path.exists()


def test_successful_cleanup_publishes_canonical_artifacts(tmp_path: Path) -> None:
    settings, intake, raw, _, _ = create_phase4_package(tmp_path)
    provider = FakeCleanupProvider(
        usage=CleanupUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    )
    raw_json_before = (intake.meeting_dir / "transcript" / "raw.json").read_bytes()
    raw_text_before = (intake.meeting_dir / "transcript" / "raw.txt").read_bytes()
    normalized_before = (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).read_bytes()

    result = make_service(settings, provider).clean_transcript(intake.meeting_id)

    cleaned_json_path, cleaned_text_path, metadata_path = cleanup_paths(intake)
    assert result.reused_existing is False
    assert result.cleaned_json_path == cleaned_json_path.resolve(strict=False)
    assert result.cleaned_text_path == cleaned_text_path.resolve(strict=False)
    assert result.cleanup_metadata_path == metadata_path.resolve(strict=False)
    assert provider.requests[0].model == CLEANUP_MODEL
    assert provider.requests[0].prompt_version == CLEANUP_PROMPT_VERSION
    assert provider.requests[0].response_format_name == CLEANUP_RESPONSE_FORMAT_NAME
    assert provider.requests[0].segments[0].speaker_label == "A"
    assert cleaned_json_path.exists()
    assert cleaned_text_path.exists()
    assert metadata_path.exists()
    assert [segment.segment_id for segment in result.transcript.segments] == [
        segment.segment_id for segment in raw.segments
    ]
    assert [segment.start_seconds for segment in result.transcript.segments] == [
        segment.start_seconds for segment in raw.segments
    ]
    assert [segment.speaker_label for segment in result.transcript.segments] == [
        segment.speaker_label for segment in raw.segments
    ]
    assert result.transcript.text == "\n".join(
        segment.cleaned_text for segment in result.transcript.segments
    )
    assert cleaned_text_path.read_text(encoding="utf-8") == render_cleaned_text(
        result.transcript
    )
    assert "30/07/2026" in result.transcript.text
    assert "14:30" in result.transcript.text
    assert "€2500" in result.transcript.text
    assert "v2.4" in result.transcript.text
    assert "ABC-123" in result.transcript.text
    assert "team@example.com" in result.transcript.text
    assert "https://example.com/plan" in result.transcript.text
    assert "bonjour nous gardons cette partie en francais" in result.transcript.text
    assert "summary" not in result.transcript.text.lower()
    assert (intake.meeting_dir / "transcript" / "raw.json").read_bytes() == raw_json_before
    assert (intake.meeting_dir / "transcript" / "raw.txt").read_bytes() == raw_text_before
    assert (intake.meeting_dir / NORMALIZED_AUDIO_RELATIVE_PATH).read_bytes() == normalized_before
    assert_no_staging(intake)

    metadata = CleanupMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    assert metadata == result.metadata
    assert metadata.provider.model == CLEANUP_MODEL
    assert metadata.provider.store is False
    assert metadata.provider.strict_schema is True
    assert metadata.input.raw_structured_sha256 == file_sha256(
        intake.meeting_dir / "transcript" / "raw.json"
    )
    assert metadata.artifacts.structured_sha256 == file_sha256(cleaned_json_path)
    assert metadata.artifacts.text_sha256 == file_sha256(cleaned_text_path)
    assert metadata.artifacts.changed_segment_count >= 1
    assert metadata.usage == CleanupUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    assert str(tmp_path) not in metadata_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in cleaned_json_path.read_text(encoding="utf-8")


def test_empty_raw_transcript_publishes_without_provider_request(tmp_path: Path) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path, segments=[])
    provider = FakeCleanupProvider()

    result = make_service(settings, provider).clean_transcript(intake.meeting_id)

    assert result.transcript.text == ""
    assert result.transcript.segments == []
    assert result.metadata.batching.batch_count == 0
    assert result.metadata.batching.provider_request_count == 0
    assert not provider.requests
    assert result.cleaned_text_path.read_text(encoding="utf-8") == "\n"


@pytest.mark.parametrize(
    ("mutation", "error_type"),
    [
        (lambda intake: shutil.rmtree(intake.meeting_dir), CleanupInputNotFoundError),
        (lambda intake: (intake.meeting_dir / "metadata" / "meeting.json").unlink(), CleanupInputNotFoundError),
        (lambda intake: (intake.meeting_dir / "metadata" / "normalization.json").unlink(), CleanupInputNotFoundError),
        (lambda intake: (intake.meeting_dir / "metadata" / "transcription.json").unlink(), CleanupInputNotFoundError),
        (lambda intake: (intake.meeting_dir / "transcript" / "raw.json").unlink(), CleanupInputNotFoundError),
        (lambda intake: (intake.meeting_dir / "transcript" / "raw.txt").unlink(), CleanupInputNotFoundError),
    ],
)
def test_required_phase4_inputs_are_present_before_provider_call(
    tmp_path: Path,
    mutation: Callable[[AudioIntakeResult], None],
    error_type: type[Exception],
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    provider = FakeCleanupProvider()
    mutation(intake)

    with pytest.raises(error_type):
        make_service(settings, provider).clean_transcript(intake.meeting_id)

    assert not provider.requests


def test_invalid_meeting_id_is_rejected_before_provider_call(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    provider = FakeCleanupProvider()

    with pytest.raises(CleanupInputIntegrityError):
        make_service(settings, provider).clean_transcript("../bad")

    assert not provider.requests


@pytest.mark.parametrize(
    "mutation",
    [
        lambda intake: mutate_json(
            intake.meeting_dir / "transcript" / "raw.json",
            lambda payload: payload.__setitem__(
                "meeting_id",
                "mtg_20260720T153045123456Z_deadbeef",
            ),
        ),
        lambda intake: (intake.meeting_dir / "transcript" / "raw.json").write_text("{\n", encoding="utf-8"),
        lambda intake: (intake.meeting_dir / "metadata" / "transcription.json").write_text("{\n", encoding="utf-8"),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["transcript"].__setitem__("structured_size_bytes", 999),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["transcript"].__setitem__("structured_sha256", "0" * 64),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["transcript"].__setitem__("text_size_bytes", 999),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["transcript"].__setitem__("text_sha256", "0" * 64),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["transcript"].__setitem__("segment_count", 99),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["transcript"].__setitem__("speaker_labels", ["Z"]),
        ),
        lambda intake: rewrite_raw_text_with_matching_metadata(intake, "not deterministic\n"),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["input"].__setitem__("sha256", "0" * 64),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "transcription.json",
            lambda payload: payload["provider"].__setitem__("model", "gpt-4o-transcribe"),
        ),
    ],
)
def test_input_integrity_failures_prevent_provider_call(
    tmp_path: Path,
    mutation: Callable[[AudioIntakeResult], None],
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    provider = FakeCleanupProvider()
    mutation(intake)

    with pytest.raises(CleanupInputIntegrityError):
        make_service(settings, provider).clean_transcript(intake.meeting_id)

    assert not provider.requests
    assert_cleanup_artifacts_absent(intake)


def rewrite_raw_text_with_matching_metadata(intake: AudioIntakeResult, value: str) -> None:
    raw_text_path = intake.meeting_dir / "transcript" / "raw.txt"
    raw_text_path.write_text(value, encoding="utf-8", newline="\n")
    mutate_json(
        intake.meeting_dir / "metadata" / "transcription.json",
        lambda payload: (
            payload["transcript"].__setitem__("text_size_bytes", raw_text_path.stat().st_size),
            payload["transcript"].__setitem__("text_sha256", file_sha256(raw_text_path)),
        ),
    )


def test_deterministic_batching_preserves_contiguous_order(tmp_path: Path) -> None:
    segments = [
        TranscriptSegment(
            segment_id=f"seg_{index:03d}",
            start_seconds=float(index),
            end_seconds=float(index + 1),
            speaker_label="A" if index % 2 else "B",
            text=f"segment {index} code ID-{index:03d} " + ("word " * 20),
        )
        for index in range(1, 8)
    ]
    settings = make_settings(tmp_path, cleanup_max_batch_characters=360)
    settings, intake, raw, _, _ = create_phase4_package(
        tmp_path,
        settings=settings,
        segments=segments,
        duration_seconds=8.0,
    )
    provider = FakeCleanupProvider(cleaner=lambda value: value)
    service = make_service(settings, provider)

    first_batches = [[segment.segment_id for segment in batch] for batch in service._build_batches(raw.segments)]
    second_batches = [[segment.segment_id for segment in batch] for batch in service._build_batches(raw.segments)]
    service.clean_transcript(intake.meeting_id)

    request_ids = [
        segment.segment_id for request in provider.requests for segment in request.segments
    ]
    assert first_batches == second_batches
    assert len(provider.requests) > 1
    assert request_ids == [segment.segment_id for segment in segments]
    assert len(set(request_ids)) == len(request_ids)
    assert all(request.batch_count == len(provider.requests) for request in provider.requests)
    assert all(request.batch_index == index for index, request in enumerate(provider.requests, start=1))


def test_oversize_segment_forms_single_oversize_batch(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cleanup_max_batch_characters=100)
    segments = [
        TranscriptSegment(
            segment_id="seg_001",
            start_seconds=0.0,
            end_seconds=1.0,
            speaker_label="A",
            text="ID-001 " + ("verylong " * 100),
        ),
        TranscriptSegment(
            segment_id="seg_002",
            start_seconds=1.0,
            end_seconds=2.0,
            speaker_label="B",
            text="ID-002 short",
        ),
    ]
    settings, intake, _, _, _ = create_phase4_package(
        tmp_path,
        settings=settings,
        segments=segments,
        duration_seconds=2.0,
    )
    provider = FakeCleanupProvider(cleaner=lambda value: value)

    make_service(settings, provider).clean_transcript(intake.meeting_id)

    assert [segment.segment_id for segment in provider.requests[0].segments] == ["seg_001"]
    assert [segment.segment_id for segment in provider.requests[1].segments] == ["seg_002"]


@pytest.mark.parametrize(
    "result_factory",
    [
        lambda request: CleanupProviderResult(segments=[]),
        lambda request: CleanupProviderResult(
            segments=[
                CleanupProviderSegmentResult(segment_id="seg_999", cleaned_text="Extra.")
            ]
        ),
        lambda request: CleanupProviderResult(
            segments=[
                CleanupProviderSegmentResult(segment_id="seg_002", cleaned_text="Two."),
                CleanupProviderSegmentResult(segment_id="seg_001", cleaned_text="One."),
                CleanupProviderSegmentResult(segment_id="seg_003", cleaned_text="Three."),
            ]
        ),
        lambda request: CleanupProviderResult(
            segments=[
                CleanupProviderSegmentResult(segment_id="seg_001", cleaned_text="One."),
                CleanupProviderSegmentResult(segment_id="seg_001", cleaned_text="Duplicate."),
                CleanupProviderSegmentResult(segment_id="seg_003", cleaned_text="Three."),
            ]
        ),
        lambda request: CleanupProviderResult(
            segments=[
                CleanupProviderSegmentResult(segment_id="seg_001", cleaned_text=""),
                CleanupProviderSegmentResult(segment_id="seg_002", cleaned_text="Two."),
                CleanupProviderSegmentResult(segment_id="seg_003", cleaned_text="Three."),
            ]
        ),
    ],
)
def test_provider_response_mapping_errors_are_rejected(
    tmp_path: Path,
    result_factory: Callable[[CleanupProviderRequest], CleanupProviderResult],
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)

    with pytest.raises(CleanupProviderResponseError):
        make_service(
            settings,
            FakeCleanupProvider(result_factory=result_factory),
        ).clean_transcript(intake.meeting_id)

    assert_cleanup_artifacts_absent(intake)
    assert_no_staging(intake)


def test_protected_tokens_and_language_preservation_are_accepted(tmp_path: Path) -> None:
    raw_text = (
        "please keep 42 on 30/07/2026 at 14:30, 75%, €2500, "
        "v2.4, ABC-123, team@example.com, https://example.com/plan, "
        "and bonjour nous gardons cette partie en francais"
    )
    segments = [
        TranscriptSegment(
            segment_id="seg_001",
            start_seconds=0.0,
            end_seconds=1.0,
            speaker_label="A",
            text=raw_text,
        )
    ]
    settings, intake, _, _, _ = create_phase4_package(tmp_path, segments=segments)

    result = make_service(
        settings,
        FakeCleanupProvider(cleaner=lambda value: value[:1].upper() + value[1:] + "."),
    ).clean_transcript(intake.meeting_id)

    cleaned = result.transcript.segments[0].cleaned_text
    for token in [
        "42",
        "30/07/2026",
        "14:30",
        "75%",
        "€2500",
        "v2.4",
        "ABC-123",
        "team@example.com",
        "https://example.com/plan",
    ]:
        assert token in cleaned
    assert "bonjour nous gardons cette partie en francais" in cleaned


@pytest.mark.parametrize(
    ("cleaned_text", "error_type"),
    [
        ("The pilot starts at 14:30 and budget is €2500.", CleanupFidelityError),
        (
            "The pilot starts on 30/07/2026 at 14:30 and budget is €2500 with ID-999.",
            CleanupFidelityError,
        ),
        (
            "The pilot starts on 30/07/2026 30/07/2026 at 14:30 and budget is €2500.",
            CleanupFidelityError,
        ),
        ("", CleanupProviderResponseError),
        (
            "The pilot starts on 30/07/2026 at 14:30 and budget is €2500. "
            + ("extra " * 200),
            CleanupFidelityError,
        ),
    ],
)
def test_fidelity_violations_are_rejected(
    tmp_path: Path,
    cleaned_text: str,
    error_type: type[Exception],
) -> None:
    segments = [
        TranscriptSegment(
            segment_id="seg_001",
            start_seconds=0.0,
            end_seconds=1.0,
            speaker_label="A",
            text="The pilot starts on 30/07/2026 at 14:30 and budget is €2500",
        )
    ]
    settings, intake, _, _, _ = create_phase4_package(tmp_path, segments=segments)

    with pytest.raises(error_type):
        make_service(
            settings,
            FakeCleanupProvider(cleaner=lambda _value: cleaned_text),
        ).clean_transcript(intake.meeting_id)

    assert_cleanup_artifacts_absent(intake)


def test_usage_aggregates_across_batches(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cleanup_max_batch_characters=260)
    segments = [
        TranscriptSegment(
            segment_id=f"seg_{index:03d}",
            start_seconds=float(index),
            end_seconds=float(index + 1),
            speaker_label="A",
            text=f"segment ID-{index:03d} " + ("word " * 20),
        )
        for index in range(1, 5)
    ]
    settings, intake, _, _, _ = create_phase4_package(
        tmp_path,
        settings=settings,
        segments=segments,
        duration_seconds=5.0,
    )
    provider = FakeCleanupProvider(
        cleaner=lambda value: value,
        usage=CleanupUsage(
            input_tokens=1,
            output_tokens=2,
            total_tokens=3,
            cached_input_tokens=4,
            reasoning_tokens=5,
        ),
    )

    result = make_service(settings, provider).clean_transcript(intake.meeting_id)

    assert len(provider.requests) > 1
    assert result.metadata.usage == CleanupUsage(
        input_tokens=len(provider.requests),
        output_tokens=2 * len(provider.requests),
        total_tokens=3 * len(provider.requests),
        cached_input_tokens=4 * len(provider.requests),
        reasoning_tokens=5 * len(provider.requests),
    )


@pytest.mark.parametrize(
    ("provider", "error_type"),
    [
        (
            FakeCleanupProvider(error=CleanupProviderError("forced")),
            CleanupProviderError,
        ),
        (
            FakeCleanupProvider(
                error=CleanupProviderError("forced"),
                error_on_call=2,
            ),
            CleanupProviderError,
        ),
    ],
)
def test_provider_failures_publish_nothing_and_clean_staging(
    tmp_path: Path,
    provider: FakeCleanupProvider,
    error_type: type[Exception],
) -> None:
    settings = make_settings(tmp_path, cleanup_max_batch_characters=260)
    settings, intake, _, _, _ = create_phase4_package(tmp_path, settings=settings)

    with pytest.raises(error_type):
        make_service(settings, provider).clean_transcript(intake.meeting_id)

    assert_cleanup_artifacts_absent(intake)
    assert_no_staging(intake)


def test_cleaned_json_write_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    service = make_service(settings, FakeCleanupProvider())
    real_write = service._write_json_atomically

    def fail_cleaned_json(model: Any, path: Path, error_type: Any, message: str) -> None:
        if path.name == "cleaned.json":
            raise CleanupPublicationError("forced")
        real_write(model, path, error_type, message)

    monkeypatch.setattr(service, "_write_json_atomically", fail_cleaned_json)

    with pytest.raises(CleanupPublicationError):
        service.clean_transcript(intake.meeting_id)

    assert_cleanup_artifacts_absent(intake)
    assert_no_staging(intake)


def test_cleaned_text_write_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    service = make_service(settings, FakeCleanupProvider())
    real_write = service._write_text_atomically

    def fail_cleaned_text(payload: str, path: Path, error_type: Any, message: str) -> None:
        if path.name == "cleaned.txt":
            raise CleanupPublicationError("forced")
        real_write(payload, path, error_type, message)

    monkeypatch.setattr(service, "_write_text_atomically", fail_cleaned_text)

    with pytest.raises(CleanupPublicationError):
        service.clean_transcript(intake.meeting_id)

    assert_cleanup_artifacts_absent(intake)
    assert_no_staging(intake)


def test_metadata_write_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    service = make_service(settings, FakeCleanupProvider())
    real_write = service._write_json_atomically

    def fail_metadata(model: Any, path: Path, error_type: Any, message: str) -> None:
        if path.name == "cleanup.json":
            raise CleanupMetadataWriteError("forced")
        real_write(model, path, error_type, message)

    monkeypatch.setattr(service, "_write_json_atomically", fail_metadata)

    with pytest.raises(CleanupMetadataWriteError):
        service.clean_transcript(intake.meeting_id)

    assert_cleanup_artifacts_absent(intake)
    assert_no_staging(intake)


@pytest.mark.parametrize("failed_name", ["cleaned.json", "cleaned.txt", "cleanup.json"])
def test_publication_failure_rolls_back_published_cleanup_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_name: str,
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    real_replace = service_module.os.replace

    def fail_selected_publish(source: Path | str, destination: Path | str) -> None:
        destination_path = Path(destination)
        if destination_path.name == failed_name and ".staging" not in destination_path.parts:
            raise OSError("forced")
        real_replace(source, destination)

    monkeypatch.setattr(service_module.os, "replace", fail_selected_publish)

    with pytest.raises(CleanupPublicationError):
        make_service(settings, FakeCleanupProvider()).clean_transcript(intake.meeting_id)

    assert_cleanup_artifacts_absent(intake)
    assert_no_staging(intake)


def test_valid_completed_cleanup_is_reused_without_provider_call(tmp_path: Path) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    first_provider = FakeCleanupProvider()
    first = make_service(settings, first_provider).clean_transcript(intake.meeting_id)
    second_provider = FakeCleanupProvider()

    second = make_service(settings, second_provider).clean_transcript(intake.meeting_id)

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.transcript == second.transcript
    assert first.metadata == second.metadata
    assert first_provider.requests
    assert not second_provider.requests


@pytest.mark.parametrize("artifact", ["cleaned_json", "cleaned_text", "metadata"])
def test_partial_cleanup_state_is_rejected(tmp_path: Path, artifact: str) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    cleaned_json_path, cleaned_text_path, metadata_path = cleanup_paths(intake)
    if artifact == "cleaned_json":
        cleaned_json_path.parent.mkdir(parents=True, exist_ok=True)
        cleaned_json_path.write_text("{}\n", encoding="utf-8")
    elif artifact == "cleaned_text":
        cleaned_text_path.parent.mkdir(parents=True, exist_ok=True)
        cleaned_text_path.write_text("partial\n", encoding="utf-8")
    else:
        metadata_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(CleanupStateError):
        make_service(settings, FakeCleanupProvider()).clean_transcript(intake.meeting_id)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "cleanup.json",
            lambda payload: payload["provider"].__setitem__("model", "gpt-5-mini"),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "cleanup.json",
            lambda payload: payload.__setitem__("prompt_version", "other-prompt"),
        ),
        lambda intake: mutate_json(
            intake.meeting_dir / "metadata" / "cleanup.json",
            lambda payload: payload["input"].__setitem__("raw_structured_sha256", "0" * 64),
        ),
        lambda intake: (intake.meeting_dir / "transcript" / "cleaned.txt").write_text(
            "changed\n",
            encoding="utf-8",
        ),
        lambda intake: mutate_cleaned_json_segment(
            intake,
            lambda segment: segment.__setitem__("speaker_label", "Z"),
        ),
        lambda intake: mutate_cleaned_json_segment(
            intake,
            lambda segment: segment.__setitem__("cleaned_text", "ID-999 added"),
        ),
    ],
)
def test_reused_cleanup_mismatches_are_rejected(
    tmp_path: Path,
    mutation: Callable[[AudioIntakeResult], None],
) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    make_service(settings, FakeCleanupProvider()).clean_transcript(intake.meeting_id)
    mutation(intake)

    with pytest.raises(CleanupStateError):
        make_service(settings, FakeCleanupProvider()).clean_transcript(intake.meeting_id)


def mutate_cleaned_json_segment(
    intake: AudioIntakeResult,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    path = intake.meeting_dir / "transcript" / "cleaned.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload["segments"][0])
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def test_cleanup_settings_validation_and_environment_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("CONVOINTEL_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.setenv("CONVOINTEL_CLEANUP_MODEL", CLEANUP_MODEL)
    monkeypatch.setenv("CONVOINTEL_CLEANUP_TIMEOUT_SECONDS", "44")
    monkeypatch.setenv("CONVOINTEL_CLEANUP_MAX_RETRIES", "4")
    monkeypatch.setenv("CONVOINTEL_CLEANUP_MAX_BATCH_CHARACTERS", "555")
    monkeypatch.setenv("CONVOINTEL_CLEANUP_MAX_OUTPUT_TOKENS", "666")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-openai-key"
    assert "test-openai-key" not in repr(settings)
    assert settings.cleanup_model == CLEANUP_MODEL
    assert settings.cleanup_timeout_seconds == 44
    assert settings.cleanup_max_retries == 4
    assert settings.cleanup_max_batch_characters == 555
    assert settings.cleanup_max_output_tokens == 666


@pytest.mark.parametrize(
    "values",
    [
        {"cleanup_model": "gpt-5-mini"},
        {"cleanup_timeout_seconds": 0},
        {"cleanup_max_retries": 99},
        {"cleanup_max_batch_characters": 0},
        {"cleanup_max_output_tokens": 0},
    ],
)
def test_cleanup_settings_reject_invalid_values(
    tmp_path: Path,
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, **values)


def test_cleaned_transcript_model_and_rendering_are_deterministic(tmp_path: Path) -> None:
    settings, intake, _, _, _ = create_phase4_package(tmp_path)
    result = make_service(settings, FakeCleanupProvider()).clean_transcript(intake.meeting_id)
    cleaned = CleanedTranscript.model_validate_json(
        result.cleaned_json_path.read_text(encoding="utf-8")
    )

    assert cleaned == result.transcript
    assert cleaned.segments[0].raw_text_sha256 == text_sha256(
        default_raw_segments()[0].text
    )
    assert render_cleaned_text(cleaned).startswith(
        "[00:00.000-00:01.400] Speaker A:"
    )
    assert result.metadata.artifacts.changed_segment_count + result.metadata.artifacts.unchanged_segment_count == len(
        cleaned.segments
    )
