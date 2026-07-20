"""Tests for deterministic calendar recommendation policy."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.models.intelligence import DecisionIntelligenceArtifact
from backend.app.models.temporal import (
    TemporalEvidenceReference,
    TemporalIntelligenceArtifact,
    TemporalIntelligenceReference,
    TemporalItem,
)
from backend.app.services.calendar.errors import CalendarPolicyError
from backend.app.services.calendar.policy import build_calendar_recommendations
from backend.tests.test_temporal_intelligence_service import create_phase6_package

HASH = "0" * 64


def policy_inputs(
    tmp_path: Path,
    items: list[TemporalItem],
) -> tuple[DecisionIntelligenceArtifact, TemporalIntelligenceArtifact]:
    _settings, intake = create_phase6_package(tmp_path)
    intelligence = DecisionIntelligenceArtifact.model_validate_json(
        (
            intake.meeting_dir
            / "intelligence"
            / "decision_intelligence.json"
        ).read_text(encoding="utf-8")
    )
    temporal = TemporalIntelligenceArtifact(
        meeting_id=intake.meeting_id,
        source_cleaned_transcript_sha256=HASH,
        source_intelligence_sha256=HASH,
        source_intelligence_metadata_sha256=HASH,
        temporal_reference=None,
        items=items,
        gaps=[],
    )
    return intelligence, temporal


def evidence(segment_id: str = "seg_002") -> list[TemporalEvidenceReference]:
    return [
        TemporalEvidenceReference(
            segment_id=segment_id,
            speaker_label="A",
            start_seconds=1.0,
            end_seconds=1.8,
            cleaned_text_sha256=HASH,
        )
    ]


def ref(
    item_type: str = "action_item",
    item_id: str = "action_001",
) -> list[TemporalIntelligenceReference]:
    return [
        TemporalIntelligenceReference(
            item_type=item_type,
            item_id=item_id,
        )
    ]


def temporal_item(index: int, **overrides: object) -> TemporalItem:
    values: dict[str, object] = {
        "temporal_id": f"temporal_{index:03d}",
        "expression_text": "by Friday",
        "category": "deadline",
        "expression_type": "absolute",
        "resolution_status": "resolved_exact",
        "resolution_basis": "explicit_text",
        "precision": "date",
        "confidence": "high",
        "start_date": "2026-07-24",
        "start_time": None,
        "end_date": None,
        "end_time": None,
        "timezone_name": "Europe/Paris",
        "utc_offset_minutes": None,
        "start_datetime_utc": None,
        "end_datetime_utc": None,
        "duration_value": None,
        "duration_unit": None,
        "duration_seconds": None,
        "recurrence_frequency": None,
        "recurrence_interval": None,
        "recurrence_days": [],
        "evidence": evidence(),
        "related_intelligence_items": ref(),
    }
    values.update(overrides)
    return TemporalItem.model_validate(values)


def test_date_only_action_deadline_is_ready_all_day(tmp_path: Path) -> None:
    intelligence, temporal = policy_inputs(tmp_path, [temporal_item(1)])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.recommendation_id == "calendar_rec_001"
    assert recommendation.recommendation_type == "deadline"
    assert recommendation.readiness_status == "ready"
    assert recommendation.schedule.shape == "all_day"
    assert recommendation.schedule.all_day is True
    assert recommendation.schedule.start_date == "2026-07-24"
    assert recommendation.schedule.start_datetime_utc is None
    assert recommendation.title.startswith("Deadline: Alice will send v2.4")
    assert "Source type: action_item" in recommendation.description
    assert "Temporal expression: by Friday" in recommendation.description
    assert recommendation.informational_flags == ["date_only_candidate"]


def test_complete_time_window_becomes_ready_timed_event(tmp_path: Path) -> None:
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

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.recommendation_type == "event"
    assert recommendation.readiness_status == "ready"
    assert recommendation.schedule.shape == "timed"
    assert recommendation.schedule.start_time == "09:00"
    assert recommendation.schedule.end_time == "11:00"
    assert recommendation.schedule.start_datetime_utc == start_utc
    assert recommendation.schedule.end_datetime_utc == end_utc


def test_compatible_duration_derives_event_end(tmp_path: Path) -> None:
    start = temporal_item(
        1,
        expression_text="30 July 2026 at 09:00",
        category="datetime_reference",
        precision="datetime",
        start_date="2026-07-30",
        start_time="09:00",
        timezone_name="Europe/Paris",
        start_datetime_utc=datetime(2026, 7, 30, 7, tzinfo=timezone.utc),
        related_intelligence_items=ref("decision", "decision_001"),
        evidence=evidence("seg_001"),
    )
    duration = temporal_item(
        2,
        expression_text="90 minutes",
        category="duration",
        expression_type="duration",
        precision="duration",
        start_date=None,
        timezone_name=None,
        duration_value=90,
        duration_unit="minutes",
        duration_seconds=5400,
        related_intelligence_items=ref("decision", "decision_001"),
        evidence=evidence("seg_001"),
    )
    intelligence, temporal = policy_inputs(tmp_path, [start, duration])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.schedule.shape == "timed"
    assert recommendation.schedule.end_date == "2026-07-30"
    assert recommendation.schedule.end_time == "10:30"
    assert recommendation.schedule.end_datetime_utc == datetime(
        2026,
        7,
        30,
        8,
        30,
        tzinfo=timezone.utc,
    )
    assert "end_derived_from_duration" in recommendation.informational_flags


def test_unrelated_duration_does_not_enrich_event(tmp_path: Path) -> None:
    start = temporal_item(
        1,
        expression_text="30 July 2026 at 09:00",
        category="datetime_reference",
        precision="datetime",
        start_date="2026-07-30",
        start_time="09:00",
        timezone_name="Europe/Paris",
        related_intelligence_items=ref("decision", "decision_001"),
        evidence=evidence("seg_001"),
    )
    duration = temporal_item(
        2,
        expression_text="90 minutes",
        category="duration",
        expression_type="duration",
        precision="duration",
        start_date=None,
        timezone_name=None,
        duration_value=90,
        duration_unit="minutes",
        duration_seconds=5400,
        related_intelligence_items=[],
    )
    intelligence, temporal = policy_inputs(tmp_path, [start, duration])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.schedule.shape == "unscheduled"
    assert "missing_end" in recommendation.review_reasons
    assert result.exclusions[0].reason == "standalone_duration"


def test_recurrence_missing_time_needs_review_without_rrule(tmp_path: Path) -> None:
    item = temporal_item(
        1,
        expression_text="every Tuesday",
        category="recurrence",
        expression_type="recurring",
        precision="recurrence",
        start_date="2026-07-28",
        start_time=None,
        recurrence_frequency="weekly",
        recurrence_interval=1,
        recurrence_days=["tuesday"],
        related_intelligence_items=ref("decision", "decision_001"),
        evidence=evidence("seg_001"),
    )
    intelligence, temporal = policy_inputs(tmp_path, [item])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.recommendation_type == "recurring_event"
    assert recommendation.readiness_status == "needs_review"
    assert recommendation.review_reasons == ["recurrence_missing_time"]
    payload = recommendation.schedule.model_dump(mode="json")
    assert "rrule" not in {key.lower() for key in payload}


def test_unresolved_reminder_request_is_not_scheduled(tmp_path: Path) -> None:
    item = temporal_item(
        1,
        expression_text="remind me before the workshop",
        category="reminder_request",
        expression_type="relative",
        resolution_status="unresolved",
        resolution_basis="insufficient_information",
        precision="unknown",
        start_date=None,
        timezone_name=None,
        related_intelligence_items=ref("decision", "decision_001"),
        evidence=evidence("seg_001"),
    )
    intelligence, temporal = policy_inputs(tmp_path, [item])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.recommendation_type == "reminder_request"
    assert recommendation.readiness_status == "needs_review"
    assert recommendation.schedule.shape == "unscheduled"
    assert recommendation.schedule.reminder_expression_text == item.expression_text
    assert "reminder_trigger_unresolved" in recommendation.review_reasons


def test_ambiguous_milestone_remains_unscheduled(tmp_path: Path) -> None:
    item = temporal_item(
        1,
        expression_text="in August",
        category="milestone",
        expression_type="vague",
        resolution_status="ambiguous",
        resolution_basis="insufficient_information",
        precision="month",
        start_date=None,
        timezone_name=None,
        related_intelligence_items=ref("decision", "decision_001"),
        evidence=evidence("seg_001"),
    )
    intelligence, temporal = policy_inputs(tmp_path, [item])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.recommendation_type == "milestone"
    assert recommendation.readiness_status == "needs_review"
    assert recommendation.schedule.shape == "unscheduled"
    assert "ambiguous_temporal" in recommendation.review_reasons
    assert recommendation.schedule.start_date is None


def test_exact_duplicate_candidates_merge(tmp_path: Path) -> None:
    first = temporal_item(1)
    second = temporal_item(
        2,
        expression_text="by Friday",
        evidence=evidence("seg_002"),
    )
    intelligence, temporal = policy_inputs(tmp_path, [first, second])

    result = build_calendar_recommendations(intelligence, temporal)

    assert len(result.recommendations) == 1
    recommendation = result.recommendations[0]
    assert recommendation.source_temporal_ids == ["temporal_001", "temporal_002"]
    assert recommendation.merged_source_count == 2
    assert "duplicate_sources_merged" in recommendation.informational_flags
    assert len(recommendation.deduplication_key_sha256) == 64


def test_conflicting_deadlines_are_blocked_without_selecting_value(tmp_path: Path) -> None:
    first = temporal_item(1, expression_text="by Friday", start_date="2026-07-24")
    second = temporal_item(2, expression_text="by Monday", start_date="2026-07-27")
    intelligence, temporal = policy_inputs(tmp_path, [first, second])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.readiness_status == "blocked"
    assert recommendation.schedule.shape == "unscheduled"
    assert "multiple_distinct_schedules" in recommendation.blocking_reasons
    assert recommendation.source_temporal_ids == ["temporal_001", "temporal_002"]


def test_standalone_and_unsupported_items_become_exclusions(tmp_path: Path) -> None:
    duration = temporal_item(
        1,
        expression_text="for two hours",
        category="duration",
        expression_type="duration",
        precision="duration",
        start_date=None,
        timezone_name=None,
        duration_value=2,
        duration_unit="hours",
        duration_seconds=7200,
        related_intelligence_items=[],
    )
    unsupported = temporal_item(
        2,
        expression_text="soon",
        category="other_temporal",
        expression_type="unknown",
        resolution_status="unresolved",
        resolution_basis="insufficient_information",
        precision="unknown",
        start_date=None,
        timezone_name=None,
        related_intelligence_items=[],
    )
    intelligence, temporal = policy_inputs(tmp_path, [duration, unsupported])

    result = build_calendar_recommendations(intelligence, temporal)

    assert result.recommendations == []
    assert [item.exclusion_id for item in result.exclusions] == [
        "calendar_exclusion_001",
        "calendar_exclusion_002",
    ]
    assert [item.reason for item in result.exclusions] == [
        "standalone_duration",
        "unsupported_temporal_category",
    ]


def test_evidence_overlap_alone_does_not_create_relationship(tmp_path: Path) -> None:
    standalone = temporal_item(
        1,
        related_intelligence_items=[],
        evidence=evidence("seg_002"),
    )
    intelligence, temporal = policy_inputs(tmp_path, [standalone])

    result = build_calendar_recommendations(intelligence, temporal)

    recommendation = result.recommendations[0]
    assert recommendation.related_intelligence_items == []
    assert recommendation.readiness_status == "needs_review"
    assert "no_related_intelligence" in recommendation.review_reasons


def test_multiple_explicit_references_create_separate_candidates(tmp_path: Path) -> None:
    item = temporal_item(
        1,
        related_intelligence_items=[
            {"item_type": "action_item", "item_id": "action_001"},
            {"item_type": "decision", "item_id": "decision_001"},
        ],
    )
    intelligence, temporal = policy_inputs(tmp_path, [item])

    result = build_calendar_recommendations(intelligence, temporal)

    assert len(result.recommendations) == 2
    assert [
        recommendation.related_intelligence_items[0].item_type
        for recommendation in result.recommendations
    ] == ["action_item", "decision"]


def test_title_truncation_is_deterministic_and_unicode_safe(tmp_path: Path) -> None:
    intelligence, temporal = policy_inputs(tmp_path, [temporal_item(1)])
    intelligence.action_items[0].description = "Plan ABC-123 " + ("alpha " * 200)

    result = build_calendar_recommendations(intelligence, temporal)

    title = result.recommendations[0].title
    assert len(title) == 500
    assert title.endswith("\u2026")
    assert "ABC-123" in title


def test_description_overflow_is_rejected(tmp_path: Path) -> None:
    intelligence, temporal = policy_inputs(tmp_path, [temporal_item(1)])
    intelligence.action_items[0].description = "x" * 10001

    with pytest.raises(CalendarPolicyError):
        build_calendar_recommendations(intelligence, temporal)
