"""Tests for deterministic calendar recommendation service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import backend.app.services.calendar.service as calendar_service_module
from backend.app.config import Settings
from backend.app.models.calendar_recommendation import (
    CALENDAR_RECOMMENDATION_GENERATOR_VERSION,
    CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH,
    CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH,
    CalendarRecommendationArtifact,
    CalendarRecommendationMetadata,
)
from backend.app.services.calendar.errors import (
    CalendarInputIntegrityError,
    CalendarInputNotFoundError,
    CalendarMetadataWriteError,
    CalendarPublicationError,
    CalendarStateError,
)
from backend.app.services.calendar.service import CalendarRecommendationService
from backend.app.services.temporal.provider import ProviderTemporalResponse
from backend.app.services.temporal.service import TemporalIntelligenceService
from backend.tests.test_decision_intelligence_service import (
    file_sha256,
    make_settings,
    mutate_json,
)
from backend.tests.test_temporal_intelligence_service import (
    FakeTemporalProvider,
    create_phase6_package,
    fixed_temporal_clock,
    phase16_hashes,
    provider_item,
    temporal_paths,
    temporal_reference,
)

FIXED_CALENDAR_AT = datetime(2026, 7, 20, 18, 0, 5, 555555, tzinfo=timezone.utc)


def fixed_calendar_clock() -> datetime:
    return FIXED_CALENDAR_AT


def create_phase7_package(
    tmp_path: Path,
    *,
    settings: Settings | None = None,
    response: ProviderTemporalResponse | None = None,
) -> tuple[Settings, Any, Any]:
    resolved_settings, intake = create_phase6_package(tmp_path, settings=settings)
    ref_dt, ref_tz = temporal_reference()
    TemporalIntelligenceService(
        resolved_settings,
        provider=FakeTemporalProvider(result=response or ProviderTemporalResponse(items=[provider_item()])),
        clock=fixed_temporal_clock,
    ).extract_temporal_intelligence(
        intake.meeting_id,
        reference_datetime=ref_dt,
        timezone_name=ref_tz,
    )
    return resolved_settings, intake, response


def make_service(settings: Settings) -> CalendarRecommendationService:
    return CalendarRecommendationService(settings, clock=fixed_calendar_clock)


def calendar_paths(intake: Any) -> tuple[Path, Path]:
    return (
        intake.meeting_dir / CALENDAR_RECOMMENDATION_JSON_RELATIVE_PATH,
        intake.meeting_dir / CALENDAR_RECOMMENDATION_METADATA_RELATIVE_PATH,
    )


def phase17_hashes(intake: Any) -> dict[str, str]:
    values = phase16_hashes(intake)
    temporal_path, temporal_metadata_path = temporal_paths(intake)
    values["temporal"] = file_sha256(temporal_path)
    values["temporal_metadata"] = file_sha256(temporal_metadata_path)
    return values


def assert_calendar_absent(intake: Any) -> None:
    for path in calendar_paths(intake):
        assert not path.exists()


def assert_no_staging(intake: Any) -> None:
    staging_root = intake.meeting_dir / ".staging"
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_successful_calendar_generation_publishes_artifacts(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    before = phase17_hashes(intake)

    result = make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    recommendations_path, metadata_path = calendar_paths(intake)
    assert result.reused_existing is False
    assert result.recommendations_json_path == recommendations_path.resolve(strict=False)
    assert result.recommendations_metadata_path == metadata_path.resolve(strict=False)
    assert result.recommendations.generator_version == CALENDAR_RECOMMENDATION_GENERATOR_VERSION
    assert result.recommendations.source_intelligence_sha256 == before["intelligence"]
    assert result.recommendations.source_temporal_sha256 == before["temporal"]
    assert len(result.recommendations.recommendations) == 1
    recommendation = result.recommendations.recommendations[0]
    assert recommendation.recommendation_id == "calendar_rec_001"
    assert recommendation.recommendation_type == "deadline"
    assert recommendation.readiness_status == "ready"
    assert recommendation.schedule.shape == "all_day"
    assert recommendation.related_intelligence_items[0].item_id == "action_001"
    assert recommendation.source_temporal_ids == ["temporal_001"]
    assert result.metadata.generator.mode == "deterministic_local"
    assert result.metadata.generator.network_access is False
    assert result.metadata.generator.provider_request_count == 0
    assert result.metadata.output.recommendations_sha256 == file_sha256(recommendations_path)
    assert result.metadata.output.recommendation_count == 1
    assert result.metadata.output.ready_count == 1
    assert result.metadata.output.needs_review_count == 0
    assert result.metadata.output.blocked_count == 0
    assert result.metadata.output.exclusion_count == 0
    assert CalendarRecommendationArtifact.model_validate_json(
        recommendations_path.read_text(encoding="utf-8")
    ) == result.recommendations
    assert CalendarRecommendationMetadata.model_validate_json(
        metadata_path.read_text(encoding="utf-8")
    ) == result.metadata
    assert str(tmp_path) not in recommendations_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in metadata_path.read_text(encoding="utf-8")
    assert phase17_hashes(intake) == before
    assert_no_staging(intake)


def test_valid_calendar_output_is_reused(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    first = make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    second = make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert first.reused_existing is False
    assert second.reused_existing is True
    assert first.recommendations == second.recommendations
    assert first.metadata == second.metadata


def test_calendar_generation_does_not_require_api_key(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, openai_api_key=None)
    settings, intake, _ = create_phase7_package(tmp_path, settings=settings)

    result = make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert result.metadata.generator.provider_request_count == 0
    assert result.metadata.generator.network_access is False


def test_empty_temporal_artifact_publishes_empty_calendar_output(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(
        tmp_path,
        response=ProviderTemporalResponse(items=[]),
    )

    result = make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert result.recommendations.recommendations == []
    assert result.recommendations.exclusions == []
    assert result.metadata.output.recommendation_count == 0
    assert result.metadata.output.exclusion_count == 0


def test_invalid_meeting_id_is_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    with pytest.raises(CalendarInputIntegrityError):
        make_service(settings).generate_calendar_recommendations("bad-id")


def test_missing_meeting_package_is_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    with pytest.raises(CalendarInputNotFoundError):
        make_service(settings).generate_calendar_recommendations(
            "mtg_20260720T153045123456Z_a1b2c3d4"
        )


def test_missing_phase7_is_rejected(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)

    with pytest.raises(CalendarInputNotFoundError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert_calendar_absent(intake)


def test_partial_phase7_state_is_rejected(tmp_path: Path) -> None:
    settings, intake = create_phase6_package(tmp_path)
    temporal_path, _metadata_path = temporal_paths(intake)
    temporal_path.parent.mkdir(parents=True)
    temporal_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(CalendarStateError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert_calendar_absent(intake)


def test_partial_calendar_state_is_rejected_without_overwrite(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    recommendations_path, metadata_path = calendar_paths(intake)
    recommendations_path.parent.mkdir(parents=True)
    recommendations_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(CalendarStateError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert recommendations_path.exists()
    assert not metadata_path.exists()


def test_reuse_rejects_source_hash_mismatch(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    make_service(settings).generate_calendar_recommendations(intake.meeting_id)
    _recommendations_path, metadata_path = calendar_paths(intake)
    mutate_json(
        metadata_path,
        lambda payload: payload["input"].__setitem__("temporal_sha256", "1" * 64),
    )

    with pytest.raises(CalendarStateError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)


def test_reuse_rejects_title_mapping_mismatch(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    make_service(settings).generate_calendar_recommendations(intake.meeting_id)
    recommendations_path, _metadata_path = calendar_paths(intake)
    mutate_json(
        recommendations_path,
        lambda payload: payload["recommendations"][0].__setitem__(
            "title",
            "Deadline: changed",
        ),
    )

    with pytest.raises(CalendarStateError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)


def test_reuse_rejects_generator_version_mismatch(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    make_service(settings).generate_calendar_recommendations(intake.meeting_id)
    recommendations_path, _metadata_path = calendar_paths(intake)
    mutate_json(
        recommendations_path,
        lambda payload: payload.__setitem__(
            "generator_version",
            "convointel-calendar-recommendations-v0",
        ),
    )

    with pytest.raises(CalendarStateError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)


def test_write_failure_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    service = make_service(settings)

    def fail_recommendations(model: Any, path: Path, error_type: Any, message: str) -> None:
        if path.name == "recommendations.json":
            raise CalendarPublicationError("forced")
        raise AssertionError("metadata should not be written")

    monkeypatch.setattr(service, "_write_json_atomically", fail_recommendations)

    with pytest.raises(CalendarPublicationError):
        service.generate_calendar_recommendations(intake.meeting_id)

    assert_calendar_absent(intake)
    assert_no_staging(intake)


def test_metadata_write_failure_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    service = make_service(settings)
    real_write = service._write_json_atomically

    def fail_metadata(model: Any, path: Path, error_type: Any, message: str) -> None:
        if path.name == "calendar_recommendations.json":
            raise CalendarMetadataWriteError("forced")
        real_write(model, path, error_type, message)

    monkeypatch.setattr(service, "_write_json_atomically", fail_metadata)

    with pytest.raises(CalendarMetadataWriteError):
        service.generate_calendar_recommendations(intake.meeting_id)

    assert_calendar_absent(intake)
    assert_no_staging(intake)


@pytest.mark.parametrize("failed_name", ["recommendations.json", "calendar_recommendations.json"])
def test_publication_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_name: str,
) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    real_replace = calendar_service_module.os.replace

    def fail_selected_publish(source: Path | str, destination: Path | str) -> None:
        destination_path = Path(destination)
        if destination_path.name == failed_name and ".staging" not in destination_path.parts:
            raise OSError("forced")
        real_replace(source, destination)

    monkeypatch.setattr(calendar_service_module.os, "replace", fail_selected_publish)

    with pytest.raises(CalendarPublicationError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert_calendar_absent(intake)
    assert_no_staging(intake)


def test_temporal_integrity_failure_prevents_calendar_output(tmp_path: Path) -> None:
    settings, intake, _ = create_phase7_package(tmp_path)
    temporal_path, _metadata_path = temporal_paths(intake)
    temporal_payload = json.loads(temporal_path.read_text(encoding="utf-8"))
    temporal_payload["items"][0]["temporal_id"] = "temporal_999"
    temporal_path.write_text(
        json.dumps(temporal_payload, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(CalendarStateError):
        make_service(settings).generate_calendar_recommendations(intake.meeting_id)

    assert_calendar_absent(intake)
