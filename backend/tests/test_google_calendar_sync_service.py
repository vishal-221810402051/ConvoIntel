"""Tests for explicitly approved Google Calendar sync service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.models.google_calendar_sync import (
    CalendarSyncApproval,
    CalendarSyncArtifact,
    CalendarSyncMetadata,
    GoogleCalendarRemoteEvent,
    sync_metadata_relative_path,
    sync_relative_path,
)
from backend.app.services.calendar.service import CalendarRecommendationService
from backend.app.services.google_calendar.errors import (
    GoogleCalendarAuthorizationRequiredError,
    GoogleCalendarLocalPersistenceAfterRemoteSuccessError,
    GoogleCalendarPublicationError,
    GoogleCalendarRecommendationNotReadyError,
    GoogleCalendarRemoteConflictError,
    GoogleCalendarSyncStateError,
)
from backend.app.services.google_calendar.service import GoogleCalendarSyncService
from backend.app.services.temporal.provider import ProviderTemporalResponse
from backend.tests.test_calendar_recommendation_service import (
    create_phase7_package,
    fixed_calendar_clock,
)
from backend.tests.test_decision_intelligence_service import file_sha256
from backend.tests.test_temporal_intelligence_service import provider_item

FIXED_SYNC_AT = datetime(2026, 7, 21, 9, 30, 0, 123456, tzinfo=timezone.utc)


def fixed_sync_clock() -> datetime:
    return FIXED_SYNC_AT


class FakeGoogleGateway:
    def __init__(self) -> None:
        self.events: dict[str, GoogleCalendarRemoteEvent] = {}
        self.calls: list[tuple[str, str]] = []
        self.conflict_on_insert = False

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
    ) -> GoogleCalendarRemoteEvent | None:
        self.calls.append(("get", event_id))
        return self.events.get(event_id)

    def insert_event(
        self,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
    ) -> GoogleCalendarRemoteEvent:
        self.calls.append(("insert", event_id))
        remote = remote_from_body(event_id, body)
        self.events[event_id] = remote
        if self.conflict_on_insert:
            raise GoogleCalendarRemoteConflictError("duplicate")
        return remote


def remote_from_body(event_id: str, body: dict[str, Any]) -> GoogleCalendarRemoteEvent:
    return GoogleCalendarRemoteEvent(
        event_id=event_id,
        html_link="https://calendar.google.com/event",
        status="confirmed",
        summary=body["summary"],
        start=body.get("start"),
        end=body.get("end"),
        recurrence=body.get("recurrence"),
        private_extended_properties=body["extendedProperties"]["private"],
    )


def publish_phase8_package(tmp_path: Path, response: ProviderTemporalResponse | None = None):
    settings, intake, _ = create_phase7_package(tmp_path, response=response)
    CalendarRecommendationService(
        settings,
        clock=fixed_calendar_clock,
    ).generate_calendar_recommendations(intake.meeting_id)
    return settings, intake


def make_service(settings) -> GoogleCalendarSyncService:
    return GoogleCalendarSyncService(settings, clock=fixed_sync_clock)


def approval() -> CalendarSyncApproval:
    return CalendarSyncApproval(recommendation_id="calendar_rec_001", confirmed=True)


def test_explicit_approval_is_required() -> None:
    with pytest.raises(Exception):
        CalendarSyncApproval(recommendation_id="calendar_rec_001")
    with pytest.raises(Exception):
        CalendarSyncApproval(recommendation_id="calendar_rec_001", confirmed=False)


def test_successful_sync_creates_artifacts_and_metadata(tmp_path: Path) -> None:
    settings, intake = publish_phase8_package(tmp_path)
    before_hash = file_sha256(intake.meeting_dir / "calendar" / "recommendations.json")
    gateway = FakeGoogleGateway()

    result = make_service(settings).sync_approved_calendar_recommendation(
        intake.meeting_id,
        approval(),
        calendar_id="phase9-test-calendar",
        gateway=gateway,
    )

    sync_path = intake.meeting_dir / sync_relative_path("calendar_rec_001")
    metadata_path = intake.meeting_dir / sync_metadata_relative_path("calendar_rec_001")
    assert result.operation == "created"
    assert result.reused_existing is False
    assert result.sync_json_path == sync_path.resolve(strict=False)
    assert result.sync_metadata_path == metadata_path.resolve(strict=False)
    assert gateway.calls == [
        ("get", result.remote_event.event_id),
        ("insert", result.remote_event.event_id),
    ]
    assert CalendarSyncArtifact.model_validate_json(
        sync_path.read_text(encoding="utf-8")
    ) == result.sync
    assert CalendarSyncMetadata.model_validate_json(
        metadata_path.read_text(encoding="utf-8")
    ) == result.metadata
    assert result.sync.approval.source == "explicit_runtime"
    assert result.sync.approval.approved_at_utc == FIXED_SYNC_AT
    assert result.metadata.output.sync_sha256 == file_sha256(sync_path)
    assert result.metadata.output.google_event_id == result.remote_event.event_id
    assert str(tmp_path) not in sync_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in metadata_path.read_text(encoding="utf-8")
    assert file_sha256(intake.meeting_dir / "calendar" / "recommendations.json") == before_hash


def test_existing_local_sync_is_reused_after_remote_verification(tmp_path: Path) -> None:
    settings, intake = publish_phase8_package(tmp_path)
    gateway = FakeGoogleGateway()
    first = make_service(settings).sync_approved_calendar_recommendation(
        intake.meeting_id,
        approval(),
        calendar_id="phase9-test-calendar",
        gateway=gateway,
    )
    gateway.calls.clear()

    second = make_service(settings).sync_approved_calendar_recommendation(
        intake.meeting_id,
        approval(),
        calendar_id="phase9-test-calendar",
        gateway=gateway,
    )

    assert second.reused_existing is True
    assert second.operation == "reused_existing"
    assert second.sync == first.sync
    assert gateway.calls == [("get", first.remote_event.event_id)]


def test_remote_duplicate_is_recovered_by_deterministic_event_id(tmp_path: Path) -> None:
    settings, intake = publish_phase8_package(tmp_path)
    gateway = FakeGoogleGateway()
    gateway.conflict_on_insert = True

    result = make_service(settings).sync_approved_calendar_recommendation(
        intake.meeting_id,
        approval(),
        calendar_id="phase9-test-calendar",
        gateway=gateway,
    )

    assert result.operation == "reused_existing"
    assert gateway.calls == [
        ("get", result.remote_event.event_id),
        ("insert", result.remote_event.event_id),
        ("get", result.remote_event.event_id),
    ]


def test_partial_local_sync_state_blocks_remote_access(tmp_path: Path) -> None:
    settings, intake = publish_phase8_package(tmp_path)
    sync_path = intake.meeting_dir / sync_relative_path("calendar_rec_001")
    sync_path.parent.mkdir(parents=True)
    sync_path.write_text("{}\n", encoding="utf-8")
    gateway = FakeGoogleGateway()

    with pytest.raises(GoogleCalendarSyncStateError):
        make_service(settings).sync_approved_calendar_recommendation(
            intake.meeting_id,
            approval(),
            calendar_id="phase9-test-calendar",
            gateway=gateway,
        )

    assert gateway.calls == []


def test_not_ready_recommendation_blocks_remote_access(tmp_path: Path) -> None:
    response = ProviderTemporalResponse(
        items=[
            provider_item(
                expression_text="approved",
                category="datetime_reference",
                expression_type="absolute",
                resolution_status="resolved_exact",
                resolution_basis="explicit_text",
                precision="datetime",
                start_date="2026-07-24",
                start_time="09:00",
                end_date=None,
                end_time=None,
                timezone_name="Europe/Paris",
                evidence_segment_ids=["seg_001"],
                related_intelligence_items=[
                    {"item_type": "decision", "item_id": "decision_001"}
                ],
            )
        ]
    )
    settings, intake = publish_phase8_package(tmp_path, response=response)
    gateway = FakeGoogleGateway()

    with pytest.raises(GoogleCalendarRecommendationNotReadyError):
        make_service(settings).sync_approved_calendar_recommendation(
            intake.meeting_id,
            approval(),
            calendar_id="phase9-test-calendar",
            gateway=gateway,
        )

    assert gateway.calls == []


def test_gateway_is_not_loaded_until_after_eligibility(tmp_path: Path) -> None:
    settings, intake = publish_phase8_package(tmp_path)
    settings.google_calendar_token_path = tmp_path / "missing-token.json"

    with pytest.raises(GoogleCalendarAuthorizationRequiredError):
        make_service(settings).sync_approved_calendar_recommendation(
            intake.meeting_id,
            approval(),
            calendar_id="phase9-test-calendar",
        )


def test_local_publish_failure_keeps_remote_and_records_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, intake = publish_phase8_package(tmp_path)
    service = make_service(settings)
    gateway = FakeGoogleGateway()

    def fail_publish(*args: Any, **kwargs: Any) -> None:
        raise GoogleCalendarPublicationError("forced")

    monkeypatch.setattr(service, "_publish_artifacts", fail_publish)

    with pytest.raises(GoogleCalendarLocalPersistenceAfterRemoteSuccessError) as exc_info:
        service.sync_approved_calendar_recommendation(
            intake.meeting_id,
            approval(),
            calendar_id="phase9-test-calendar",
            gateway=gateway,
        )

    assert exc_info.value.event_id in gateway.events
    attempts = list(
        (intake.meeting_dir / "metadata" / "calendar_sync_attempts").rglob("*.json")
    )
    assert len(attempts) == 1
    payload = json.loads(attempts[0].read_text(encoding="utf-8"))
    assert payload["outcome"] == "remote_created_local_persistence_failed"
    assert "access_token" not in json.dumps(payload)
    assert not (intake.meeting_dir / sync_relative_path("calendar_rec_001")).exists()
