"""Tests for pure Google Calendar payload construction."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.models.calendar_recommendation import (
    CalendarEvidenceReference,
    CalendarIntelligenceReference,
    CalendarRecommendation,
    CalendarRecurrence,
    CalendarSchedule,
)
from backend.app.services.calendar.policy import build_calendar_recommendations
from backend.app.services.google_calendar.errors import (
    GoogleCalendarPayloadError,
    GoogleCalendarRecommendationNotReadyError,
    GoogleCalendarRecommendationUnsupportedError,
)
from backend.app.services.google_calendar.payload import (
    GOOGLE_EVENT_ID_PATTERN,
    PRIVATE_PROPERTY_MEETING_ID,
    PRIVATE_PROPERTY_RECOMMENDATION_HASH,
    PRIVATE_PROPERTY_RECOMMENDATION_ID,
    PRIVATE_PROPERTY_SYNC_VERSION,
    build_google_calendar_event_payload,
    deterministic_google_event_id,
)
from backend.tests.test_calendar_recommendation_policy import (
    evidence,
    policy_inputs,
    ref,
    temporal_item,
)

MEETING_ID = "mtg_20260720T153045123456Z_a1b2c3d4"
SOURCE_SHA = "1" * 64


def test_all_day_deadline_payload_is_safe_and_deterministic(tmp_path):
    intelligence, temporal = policy_inputs(tmp_path, [temporal_item(1)])
    recommendation = build_calendar_recommendations(
        intelligence,
        temporal,
    ).recommendations[0]

    prepared = build_google_calendar_event_payload(
        meeting_id=MEETING_ID,
        calendar_id="primary",
        recommendations_sha256=SOURCE_SHA,
        recommendation=recommendation,
    )
    again = build_google_calendar_event_payload(
        meeting_id=MEETING_ID,
        calendar_id="primary",
        recommendations_sha256=SOURCE_SHA,
        recommendation=recommendation,
    )

    assert prepared == again
    assert GOOGLE_EVENT_ID_PATTERN.fullmatch(prepared.event_id)
    assert prepared.body["id"] == prepared.event_id
    assert prepared.body["summary"] == recommendation.title
    assert prepared.body["start"] == {"date": "2026-07-24"}
    assert prepared.body["end"] == {"date": "2026-07-25"}
    assert prepared.body["reminders"] == {"useDefault": True}
    assert "Convointel meeting ID: " + MEETING_ID in prepared.body["description"]
    assert "Convointel recommendation ID: calendar_rec_001" in prepared.body["description"]
    assert "attendees" not in prepared.body
    assert "location" not in prepared.body
    assert "conferenceData" not in prepared.body
    private = prepared.body["extendedProperties"]["private"]
    assert private[PRIVATE_PROPERTY_MEETING_ID] == MEETING_ID
    assert private[PRIVATE_PROPERTY_RECOMMENDATION_ID] == "calendar_rec_001"
    assert private[PRIVATE_PROPERTY_RECOMMENDATION_HASH] == SOURCE_SHA
    assert private[PRIVATE_PROPERTY_SYNC_VERSION] == "convointel-google-calendar-sync-v1"


def test_timed_event_payload_preserves_timezone_and_validates_utc(tmp_path):
    start_utc = datetime(2026, 7, 30, 7, tzinfo=timezone.utc)
    end_utc = datetime(2026, 7, 30, 9, tzinfo=timezone.utc)
    item = temporal_item(
        1,
        expression_text="30 July 2026 from 09:00 to 11:00",
        category="time_window",
        precision="range",
        start_date="2026-07-30",
        start_time="09:00",
        end_date="2026-07-30",
        end_time="11:00",
        start_datetime_utc=start_utc,
        end_datetime_utc=end_utc,
        related_intelligence_items=ref("decision", "decision_001"),
        evidence=evidence("seg_001"),
    )
    intelligence, temporal = policy_inputs(tmp_path, [item])
    recommendation = build_calendar_recommendations(
        intelligence,
        temporal,
    ).recommendations[0]

    prepared = build_google_calendar_event_payload(
        meeting_id=MEETING_ID,
        calendar_id="primary",
        recommendations_sha256=SOURCE_SHA,
        recommendation=recommendation,
    )

    assert prepared.body["start"] == {
        "dateTime": "2026-07-30T09:00:00+02:00",
        "timeZone": "Europe/Paris",
    }
    assert prepared.body["end"] == {
        "dateTime": "2026-07-30T11:00:00+02:00",
        "timeZone": "Europe/Paris",
    }


def test_recurring_event_payload_builds_supported_rrule():
    recommendation = ready_recurring_recommendation()

    prepared = build_google_calendar_event_payload(
        meeting_id=MEETING_ID,
        calendar_id="primary",
        recommendations_sha256=SOURCE_SHA,
        recommendation=recommendation,
    )

    assert prepared.body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TU"]
    assert prepared.body["start"]["timeZone"] == "Europe/Paris"


def test_event_id_changes_with_calendar_or_source_hash():
    base = deterministic_google_event_id(
        calendar_id="primary",
        meeting_id=MEETING_ID,
        recommendation_id="calendar_rec_001",
        recommendations_sha256="1" * 64,
        deduplication_key_sha256="2" * 64,
    )
    changed = deterministic_google_event_id(
        calendar_id="test-calendar",
        meeting_id=MEETING_ID,
        recommendation_id="calendar_rec_001",
        recommendations_sha256="1" * 64,
        deduplication_key_sha256="2" * 64,
    )

    assert base != changed


def test_payload_rejects_unapproved_readiness_state():
    recommendation = ready_recurring_recommendation()
    recommendation = recommendation.model_copy(update={"readiness_status": "needs_review"})

    with pytest.raises(GoogleCalendarRecommendationNotReadyError):
        build_google_calendar_event_payload(
            meeting_id=MEETING_ID,
            calendar_id="primary",
            recommendations_sha256=SOURCE_SHA,
            recommendation=recommendation,
        )


def test_payload_rejects_unsupported_reminder():
    recommendation = ready_recurring_recommendation().model_copy(
        update={
            "recommendation_type": "reminder_request",
            "schedule": CalendarSchedule(
                shape="unscheduled",
                all_day=None,
                start_date=None,
                start_time=None,
                end_date=None,
                end_time=None,
                timezone_name=None,
                start_datetime_utc=None,
                end_datetime_utc=None,
                duration_minutes=None,
                recurrence=None,
                reminder_expression_text="remind me later",
            ),
        }
    )

    with pytest.raises(GoogleCalendarRecommendationUnsupportedError):
        build_google_calendar_event_payload(
            meeting_id=MEETING_ID,
            calendar_id="primary",
            recommendations_sha256=SOURCE_SHA,
            recommendation=recommendation,
        )


def test_timed_payload_rejects_utc_mismatch():
    recommendation = ready_recurring_recommendation()
    schedule = recommendation.schedule.model_copy(
        update={
            "shape": "timed",
            "recurrence": None,
            "start_datetime_utc": datetime(2026, 7, 28, 8, tzinfo=timezone.utc),
        }
    )
    recommendation = recommendation.model_copy(
        update={"recommendation_type": "event", "schedule": schedule}
    )

    with pytest.raises(GoogleCalendarPayloadError):
        build_google_calendar_event_payload(
            meeting_id=MEETING_ID,
            calendar_id="primary",
            recommendations_sha256=SOURCE_SHA,
            recommendation=recommendation,
        )


def ready_recurring_recommendation() -> CalendarRecommendation:
    return CalendarRecommendation(
        recommendation_id="calendar_rec_001",
        recommendation_type="recurring_event",
        readiness_status="ready",
        title="Recurring: Tuesday planning",
        description="Source type: decision\nSource ID: decision_001\nTemporal expression: every Tuesday",
        schedule=CalendarSchedule(
            shape="recurring",
            all_day=False,
            start_date="2026-07-28",
            start_time="09:00",
            end_date="2026-07-28",
            end_time="10:00",
            timezone_name="Europe/Paris",
            start_datetime_utc=datetime(2026, 7, 28, 7, tzinfo=timezone.utc),
            end_datetime_utc=datetime(2026, 7, 28, 8, tzinfo=timezone.utc),
            duration_minutes=60,
            recurrence=CalendarRecurrence(
                frequency="weekly",
                interval=1,
                days=["tuesday"],
            ),
            reminder_expression_text=None,
        ),
        source_temporal_ids=["temporal_001"],
        related_intelligence_items=[
            CalendarIntelligenceReference(
                item_type="decision",
                item_id="decision_001",
            )
        ],
        evidence=[
            CalendarEvidenceReference(
                segment_id="seg_001",
                speaker_label="A",
                start_seconds=0,
                end_seconds=1,
                cleaned_text_sha256="0" * 64,
            )
        ],
        review_reasons=[],
        blocking_reasons=[],
        informational_flags=[],
        deduplication_key_sha256="2" * 64,
        merged_source_count=1,
    )
