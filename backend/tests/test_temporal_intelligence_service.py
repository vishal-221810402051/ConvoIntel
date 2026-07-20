"""Tests for canonical temporal intelligence service."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import backend.app.services.temporal.service as temporal_service_module
from backend.app.config import TEMPORAL_MODEL, Settings, get_settings
from backend.app.models.intelligence import IntelligenceUsage
from backend.app.models.meeting import AudioIntakeResult
from backend.app.models.temporal import (
    TEMPORAL_JSON_RELATIVE_PATH,
    TEMPORAL_METADATA_RELATIVE_PATH,
    TEMPORAL_PROMPT_VERSION,
    TEMPORAL_REASONING_EFFORT,
    TEMPORAL_RESPONSE_SCHEMA_NAME,
    TemporalIntelligenceArtifact,
    TemporalMetadata,
)
from backend.app.services.intelligence.service import DecisionIntelligenceService
from backend.app.services.temporal.errors import (
    TemporalConfigurationError,
    TemporalEvidenceError,
    TemporalInputNotFoundError,
    TemporalInputTooLargeError,
    TemporalIntelligenceReferenceError,
    TemporalNormalizationError,
    TemporalProviderError,
    TemporalProviderResponseError,
    TemporalPublicationError,
    TemporalStateError,
)
from backend.app.services.temporal.provider import (
    ProviderTemporalItem,
    ProviderTemporalResponse,
    TemporalProviderRequest,
    TemporalProviderResult,
)
from backend.app.services.temporal.service import TemporalIntelligenceService
from backend.tests.test_decision_intelligence_service import (
    FakeIntelligenceProvider,
    create_phase5_package,
    file_sha256,
    fixed_intelligence_clock,
    intelligence_paths,
    make_settings,
    mutate_json,
    phase15_hashes,
    valid_provider_intelligence,
)

FIXED_TEMPORAL_AT = datetime(2026, 7, 20, 17, 0, 4, 444444, tzinfo=timezone.utc)


class FakeTemporalProvider:
    def __init__(
        self,
        result: ProviderTemporalResponse | None = None,
        *,
        error: Exception | None = None,
        usage: IntelligenceUsage | None = None,
    ) -> None:
        self.result = result or valid_temporal_response()
        self.error = error
        self.usage = usage
        self.requests: list[TemporalProviderRequest] = []

    def extract(self, request: TemporalProviderRequest) -> TemporalProviderResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return TemporalProviderResult(temporal=self.result, usage=self.usage)


def fixed_temporal_clock() -> datetime:
    return FIXED_TEMPORAL_AT


def temporal_reference() -> tuple[datetime, str]:
    return (
        datetime(2026, 7, 20, 10, tzinfo=timezone(timedelta(hours=2))),
        "Europe/Paris",
    )


def provider_item(**overrides: Any) -> ProviderTemporalItem:
    values: dict[str, Any] = {
        "expression_text": "by Friday",
        "category": "deadline",
        "expression_type": "relative",
        "resolution_status": "resolved_relative",
        "resolution_basis": "reference_datetime",
        "precision": "date",
        "confidence": "high",
        "start_date": "2026-07-24",
        "start_time": None,
        "end_date": None,
        "end_time": None,
        "timezone_name": "Europe/Paris",
        "utc_offset_minutes": None,
        "duration_value": None,
        "duration_unit": None,
        "recurrence_frequency": None,
        "recurrence_interval": None,
        "recurrence_days": [],
        "evidence_segment_ids": ["seg_002"],
        "related_intelligence_items": [
            {"item_type": "action_item", "item_id": "action_001"}
        ],
    }
    values.update(overrides)
    return ProviderTemporalItem.model_validate(values)


def valid_temporal_response() -> ProviderTemporalResponse:
    return ProviderTemporalResponse(items=[provider_item()])


def create_phase6_package(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
) -> tuple[Settings, AudioIntakeResult]:
    resolved_settings = settings or make_settings(tmp_path)
    resolved_settings, intake, _, _ = create_phase5_package(
        tmp_path,
        settings=resolved_settings,
    )
    DecisionIntelligenceService(
        resolved_settings,
        provider=FakeIntelligenceProvider(result=valid_provider_intelligence()),
        clock=fixed_intelligence_clock,
    ).analyze_meeting(intake.meeting_id)
    return resolved_settings, intake


def make_service(
    settings: Settings,
    provider: FakeTemporalProvider,
) -> TemporalIntelligenceService:
    return TemporalIntelligenceService(
        settings,
        provider=provider,
        clock=fixed_temporal_clock,
    )


def temporal_paths(intake: AudioIntakeResult) -> tuple[Path, Path]:
    return (
        intake.meeting_dir / TEMPORAL_JSON_RELATIVE_PATH,
        intake.meeting_dir / TEMPORAL_METADATA_RELATIVE_PATH,
    )


def phase16_hashes(intake: AudioIntakeResult) -> dict[str, str]:
    intelligence_path, intelligence_metadata_path = intelligence_paths(intake)
    values = phase15_hashes(intake)
    values["intelligence"] = file_sha256(intelligence_path)
    values["intelligence_metadata"] = file_sha256(intelligence_metadata_path)
    return values


def assert_temporal_absent(intake: AudioIntakeResult) -> None:
    temporal_path, metadata_path = temporal_paths(intake)
    assert not temporal_path.exists()
    assert not metadata_path.exists()


def assert_no_staging(intake: AudioIntakeResult) -> None:
    staging_root = intake.meeting_dir / ".staging"
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_successful_temporal_publishes_canonical_artifacts(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    before = phase16_hashes(intake)
    provider = FakeTemporalProvider(
        usage=IntelligenceUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    )
    ref_dt, ref_tz = temporal_reference()

    result = make_service(settings, provider).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )

    temporal_path, metadata_path = temporal_paths(intake)
    request = provider.requests[0]
    assert result.reused_existing is False
    assert result.temporal_json_path == temporal_path.resolve(strict=False)
    assert result.temporal_metadata_path == metadata_path.resolve(strict=False)
    assert request.model == TEMPORAL_MODEL
    assert request.prompt_version == TEMPORAL_PROMPT_VERSION
    assert request.response_schema_name == TEMPORAL_RESPONSE_SCHEMA_NAME
    assert request.reasoning_effort == TEMPORAL_REASONING_EFFORT
    assert request.max_output_tokens == settings.temporal_max_output_tokens
    assert request.max_items == settings.temporal_max_items
    assert "cleaned_text" in request.temporal_payload_json
    assert "raw_text_sha256" not in request.temporal_payload_json
    assert "executive_summary" not in request.temporal_payload_json
    assert "metadata" not in request.temporal_payload_json
    assert str(tmp_path) not in request.temporal_payload_json
    payload = json.loads(request.temporal_payload_json)
    assert payload["temporal_reference"]["source"] == "explicit_runtime"
    assert payload["temporal_reference"]["reference_datetime_local"].startswith(
        "2026-07-20T10:00:00"
    )
    assert payload["segments"][0]["segment_order"] == 0
    assert any(item["item_type"] == "action_item" for item in payload["intelligence_items"])
    assert result.temporal_intelligence.items[0].temporal_id == "temporal_001"
    assert result.temporal_intelligence.items[0].expression_text == "by Friday"
    assert result.temporal_intelligence.items[0].start_date == "2026-07-24"
    assert result.temporal_intelligence.items[0].start_datetime_utc is None
    assert result.temporal_intelligence.items[0].evidence[0].segment_id == "seg_002"
    assert result.temporal_intelligence.items[0].related_intelligence_items[0].item_id == "action_001"
    assert any(gap.kind == "missing_deadline" for gap in result.temporal_intelligence.gaps)
    assert result.metadata.provider.model == TEMPORAL_MODEL
    assert result.metadata.provider.store is False
    assert result.metadata.provider.reasoning_effort == "low"
    assert result.metadata.input.temporal_reference is not None
    assert result.metadata.processing.provider_request_count == 1
    assert result.metadata.processing.input_character_count == len(request.temporal_payload_json)
    assert result.metadata.output.temporal_sha256 == file_sha256(temporal_path)
    assert result.metadata.output.category_counts == result.temporal_intelligence.category_counts()
    assert result.metadata.usage == IntelligenceUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    assert TemporalIntelligenceArtifact.model_validate_json(
        temporal_path.read_text(encoding="utf-8")
    ) == result.temporal_intelligence
    assert TemporalMetadata.model_validate_json(
        metadata_path.read_text(encoding="utf-8")
    ) == result.metadata
    assert str(tmp_path) not in temporal_path.read_text(encoding="utf-8")
    assert phase16_hashes(intake) == before
    assert_no_staging(intake)


def test_valid_temporal_result_is_reused_with_same_reference(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    first_provider = FakeTemporalProvider()
    ref_dt, ref_tz = temporal_reference()
    first = make_service(settings, first_provider).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )
    second_provider = FakeTemporalProvider(error=AssertionError("should not call"))

    second = make_service(settings, second_provider).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.temporal_intelligence == second.temporal_intelligence
    assert first.metadata == second.metadata
    assert first_provider.requests
    assert not second_provider.requests


def test_reuse_requires_exact_reference(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    ref_dt, ref_tz = temporal_reference()
    make_service(settings, FakeTemporalProvider()).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )

    with pytest.raises(TemporalStateError):
        make_service(settings, FakeTemporalProvider()).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=datetime(
                2026,
                7,
                20,
                11,
                tzinfo=timezone(timedelta(hours=2)),
            ),
            timezone_name=ref_tz,
        )


def test_reference_arguments_must_be_supplied_together(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider()

    with pytest.raises(TemporalConfigurationError):
        make_service(settings, provider).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=datetime(2026, 7, 20, 10, tzinfo=timezone.utc),
        )

    assert not provider.requests
    assert_temporal_absent(intake)


def test_relative_expression_without_reference_stays_unresolved(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider(
        result=ProviderTemporalResponse(
            items=[
                provider_item(
                    resolution_status="unresolved",
                    resolution_basis="insufficient_information",
                    precision="unknown",
                    start_date=None,
                    timezone_name=None,
                )
            ]
        )
    )

    result = make_service(settings, provider).extract_temporal_intelligence(
        intake.meeting_id
    )

    assert result.temporal_intelligence.temporal_reference is None
    assert result.temporal_intelligence.items[0].start_date is None
    assert any(gap.kind == "unresolved_expression" for gap in result.temporal_intelligence.gaps)
    assert any(gap.kind == "missing_reference" for gap in result.temporal_intelligence.gaps)


def test_explicit_empty_provider_links_are_preserved(
    tmp_path: Path,
) -> None:
    """explicit empty related_intelligence_items is accepted and preserved."""

    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider(
        result=ProviderTemporalResponse(
            items=[
                provider_item(
                    related_intelligence_items=[],
                )
            ]
        )
    )
    ref_dt, ref_tz = temporal_reference()

    result = make_service(settings, provider).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )

    assert result.temporal_intelligence.items[0].related_intelligence_items == []


def test_evidence_overlap_alone_does_not_create_relationship(
    tmp_path: Path,
) -> None:
    """Evidence overlap alone must never infer an intelligence relationship."""

    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider(
        result=ProviderTemporalResponse(
            items=[
                provider_item(
                    evidence_segment_ids=["seg_002"],
                    related_intelligence_items=[],
                )
            ]
        )
    )
    ref_dt, ref_tz = temporal_reference()

    result = make_service(settings, provider).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )

    assert result.temporal_intelligence.items[0].evidence[0].segment_id == "seg_002"
    assert result.temporal_intelligence.items[0].related_intelligence_items == []


def test_relative_expression_cannot_claim_resolved_without_reference(
    tmp_path: Path,
) -> None:
    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider(result=ProviderTemporalResponse(items=[provider_item()]))

    with pytest.raises(TemporalNormalizationError):
        make_service(settings, provider).extract_temporal_intelligence(intake.meeting_id)

    assert_temporal_absent(intake)
    assert_no_staging(intake)


@pytest.mark.parametrize(
    ("mutate", "error_type"),
    [
        (
            lambda intake: (intake.meeting_dir / "intelligence" / "decision_intelligence.json").unlink(),
            TemporalStateError,
        ),
        (
            lambda intake: (intake.meeting_dir / "metadata" / "intelligence.json").unlink(),
            TemporalStateError,
        ),
        (
            lambda intake: mutate_json(
                intake.meeting_dir / "metadata" / "intelligence.json",
                lambda payload: payload["provider"].__setitem__("store", True),
            ),
            TemporalStateError,
        ),
    ],
)
def test_phase6_state_must_be_complete_and_valid_before_provider_call(
    tmp_path: Path,
    mutate: Callable[[AudioIntakeResult], None],
    error_type: type[Exception],
) -> None:
    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider()
    mutate(intake)

    with pytest.raises(error_type):
        make_service(settings, provider).extract_temporal_intelligence(intake.meeting_id)

    assert not provider.requests
    assert_temporal_absent(intake)


def test_missing_phase6_is_rejected_before_provider_call(tmp_path: Path) -> None:
    settings, intake, _, _ = create_phase5_package(tmp_path)
    provider = FakeTemporalProvider()

    with pytest.raises(TemporalInputNotFoundError):
        make_service(settings, provider).extract_temporal_intelligence(intake.meeting_id)

    assert not provider.requests


@pytest.mark.parametrize(
    ("mutation", "error_type"),
    [
        (
            lambda item: item.evidence_segment_ids.__setitem__(0, "seg_999"),
            TemporalEvidenceError,
        ),
        (
            lambda item: item.evidence_segment_ids.extend(["seg_002"]),
            TemporalEvidenceError,
        ),
        (
            lambda item: item.evidence_segment_ids.__setitem__(slice(None), ["seg_003", "seg_002"]),
            TemporalEvidenceError,
        ),
        (
            lambda item: setattr(item, "expression_text", "next Monday"),
            TemporalEvidenceError,
        ),
        (
            lambda item: item.related_intelligence_items[0].__setattr__("item_id", "action_999"),
            TemporalIntelligenceReferenceError,
        ),
        (
            lambda item: item.related_intelligence_items[0].__setattr__("item_type", "decision"),
            TemporalIntelligenceReferenceError,
        ),
    ],
)
def test_provider_grounding_failures_reject_publication(
    tmp_path: Path,
    mutation: Callable[[ProviderTemporalItem], None],
    error_type: type[Exception],
) -> None:
    settings, intake = create_phase6_package(tmp_path)
    item = provider_item()
    mutation(item)
    provider = FakeTemporalProvider(result=ProviderTemporalResponse(items=[item]))
    ref_dt, ref_tz = temporal_reference()

    with pytest.raises(error_type):
        make_service(settings, provider).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=ref_dt,
            timezone_name=ref_tz,
        )

    assert_temporal_absent(intake)
    assert_no_staging(intake)


def test_duplicate_provider_items_are_rejected(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider(
        result=ProviderTemporalResponse(items=[provider_item(), provider_item()])
    )
    ref_dt, ref_tz = temporal_reference()

    with pytest.raises(TemporalProviderResponseError):
        make_service(settings, provider).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=ref_dt,
            timezone_name=ref_tz,
        )

    assert_temporal_absent(intake)


def test_input_size_limit_rejects_without_provider_call(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, temporal_max_input_characters=10)
    settings, intake = create_phase6_package(tmp_path, settings=settings)
    provider = FakeTemporalProvider()
    ref_dt, ref_tz = temporal_reference()

    with pytest.raises(TemporalInputTooLargeError):
        make_service(settings, provider).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=ref_dt,
            timezone_name=ref_tz,
        )

    assert not provider.requests
    assert_temporal_absent(intake)
    assert_no_staging(intake)


def test_empty_cleaned_transcript_publishes_without_provider_call(tmp_path: Path) -> None:
    settings, intake, _, _ = create_phase5_package(tmp_path, segments=[])
    DecisionIntelligenceService(
        settings,
        provider=FakeIntelligenceProvider(error=AssertionError("should not call")),
        clock=fixed_intelligence_clock,
    ).analyze_meeting(intake.meeting_id)
    provider = FakeTemporalProvider(error=AssertionError("should not call"))

    result = make_service(settings, provider).extract_temporal_intelligence(
        intake.meeting_id
    )

    assert not provider.requests
    assert result.metadata.processing.provider_request_count == 0
    assert result.temporal_intelligence.items == []
    assert result.temporal_intelligence.gaps == []


def test_provider_failure_cleans_staging_and_preserves_inputs(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    before = phase16_hashes(intake)
    ref_dt, ref_tz = temporal_reference()

    with pytest.raises(TemporalProviderError):
        make_service(
            settings,
            FakeTemporalProvider(error=TemporalProviderError("forced")),
        ).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=ref_dt,
            timezone_name=ref_tz,
        )

    assert phase16_hashes(intake) == before
    assert_temporal_absent(intake)
    assert_no_staging(intake)


def test_metadata_write_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake = create_phase6_package(tmp_path)
    service = make_service(settings, FakeTemporalProvider())
    real_write = service._write_json_atomically
    ref_dt, ref_tz = temporal_reference()

    def fail_metadata(model: Any, path: Path, error_type: Any, message: str) -> None:
        if path.name == "temporal.json":
            raise TemporalPublicationError("forced")
        real_write(model, path, error_type, message)

    monkeypatch.setattr(service, "_write_json_atomically", fail_metadata)

    with pytest.raises(TemporalPublicationError):
        service.extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=ref_dt,
            timezone_name=ref_tz,
        )

    assert_temporal_absent(intake)
    assert_no_staging(intake)


@pytest.mark.parametrize("failed_name", ["temporal_intelligence.json", "temporal.json"])
def test_publication_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_name: str,
) -> None:
    settings, intake = create_phase6_package(tmp_path)
    real_replace = temporal_service_module.os.replace
    ref_dt, ref_tz = temporal_reference()

    def fail_selected_publish(source: Path | str, destination: Path | str) -> None:
        destination_path = Path(destination)
        if destination_path.name == failed_name and ".staging" not in destination_path.parts:
            raise OSError("forced")
        real_replace(source, destination)

    monkeypatch.setattr(temporal_service_module.os, "replace", fail_selected_publish)

    with pytest.raises(TemporalPublicationError):
        make_service(settings, FakeTemporalProvider()).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=ref_dt,
            timezone_name=ref_tz,
        )

    assert_temporal_absent(intake)
    assert_no_staging(intake)


def test_partial_temporal_state_is_rejected(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    temporal_path, _ = temporal_paths(intake)
    temporal_path.parent.mkdir(parents=True)
    temporal_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(TemporalStateError):
        make_service(settings, FakeTemporalProvider()).extract_temporal_intelligence(
            intake.meeting_id
        )


def test_reused_temporal_mismatches_are_rejected(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    ref_dt, ref_tz = temporal_reference()
    make_service(settings, FakeTemporalProvider()).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )
    mutate_json(
        intake.meeting_dir / "metadata" / "temporal.json",
        lambda payload: payload["provider"].__setitem__("reasoning_effort", "minimal"),
    )

    with pytest.raises(TemporalStateError):
        make_service(settings, FakeTemporalProvider()).extract_temporal_intelligence(
            intake.meeting_id,
            reference_datetime=ref_dt,
            timezone_name=ref_tz,
        )


def test_conflicting_action_deadlines_create_local_gap(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    provider = FakeTemporalProvider(
        result=ProviderTemporalResponse(
            items=[
                provider_item(start_date="2026-07-24"),
                provider_item(
                    expression_text="Friday",
                    start_date="2026-07-31",
                ),
            ]
        )
    )
    ref_dt, ref_tz = temporal_reference()

    result = make_service(settings, provider).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )

    assert any(
        gap.kind == "conflicting_temporal_information"
        for gap in result.temporal_intelligence.gaps
    )


def test_temporal_settings_validation_and_environment_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("CONVOINTEL_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.setenv("CONVOINTEL_TEMPORAL_MODEL", TEMPORAL_MODEL)
    monkeypatch.setenv("CONVOINTEL_TEMPORAL_TIMEOUT_SECONDS", "44")
    monkeypatch.setenv("CONVOINTEL_TEMPORAL_MAX_RETRIES", "4")
    monkeypatch.setenv("CONVOINTEL_TEMPORAL_MAX_INPUT_CHARACTERS", "555")
    monkeypatch.setenv("CONVOINTEL_TEMPORAL_MAX_OUTPUT_TOKENS", "666")
    monkeypatch.setenv("CONVOINTEL_TEMPORAL_MAX_ITEMS", "77")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-openai-key"
    assert "test-openai-key" not in repr(settings)
    assert settings.temporal_model == TEMPORAL_MODEL
    assert settings.temporal_timeout_seconds == 44
    assert settings.temporal_max_retries == 4
    assert settings.temporal_max_input_characters == 555
    assert settings.temporal_max_output_tokens == 666
    assert settings.temporal_max_items == 77


@pytest.mark.parametrize(
    "values",
    [
        {"temporal_model": "gpt-5-mini"},
        {"temporal_timeout_seconds": 0},
        {"temporal_max_retries": 99},
        {"temporal_max_input_characters": 0},
        {"temporal_max_output_tokens": 0},
        {"temporal_max_items": 0},
        {"temporal_max_items": 1001},
    ],
)
def test_temporal_settings_reject_invalid_values(
    tmp_path: Path,
    values: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, **values)
