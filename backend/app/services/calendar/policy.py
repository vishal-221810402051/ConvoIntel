"""Deterministic calendar recommendation policy."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.app.models.calendar_recommendation import (
    CalendarEvidenceReference,
    CalendarExclusion,
    CalendarExclusionReason,
    CalendarInformationalFlag,
    CalendarIntelligenceReference,
    CalendarRecommendation,
    CalendarRecommendationType,
    CalendarRecurrence,
    CalendarReviewReason,
    CalendarSchedule,
)
from backend.app.models.intelligence import DecisionIntelligenceArtifact
from backend.app.models.temporal import (
    TemporalEvidenceReference,
    TemporalIntelligenceArtifact,
    TemporalIntelligenceReference,
    TemporalItem,
)
from backend.app.services.calendar.errors import (
    CalendarPolicyError,
    CalendarScheduleError,
)

TITLE_LIMIT = 500
DESCRIPTION_LIMIT = 10000
TITLE_ELLIPSIS = "\u2026"
RESOLVED_STATUSES = {"resolved_exact", "resolved_relative"}
SOURCE_TEXT_FIELDS = {
    "decision": ("decision_id", "statement"),
    "action_item": ("action_id", "description"),
    "commitment": ("commitment_id", "statement"),
    "follow_up": ("follow_up_id", "description"),
    "missing_information": ("missing_info_id", "description"),
    "gap": ("gap_id", "description"),
}
RECOMMENDATION_PREFIX = {
    "event": "Event",
    "deadline": "Deadline",
    "milestone": "Milestone",
    "recurring_event": "Recurring",
    "reminder_request": "Reminder",
}
REVIEW_REASON_ORDER = [
    "ambiguous_temporal",
    "unresolved_temporal",
    "missing_start_date",
    "missing_start_time",
    "missing_end",
    "missing_timezone",
    "no_related_intelligence",
    "recurrence_missing_anchor",
    "recurrence_missing_time",
    "reminder_trigger_unresolved",
    "partial_temporal_information",
]
BLOCKING_REASON_ORDER = [
    "conflicting_temporal_information",
    "incompatible_temporal_components",
    "multiple_distinct_schedules",
    "invalid_schedule_shape",
]
FLAG_ORDER = [
    "duplicate_sources_merged",
    "end_derived_from_duration",
    "date_only_candidate",
    "standalone_temporal_candidate",
    "multiple_evidence_segments",
]


@dataclass(frozen=True)
class CalendarPolicyResult:
    """Pure policy result before artifact-level provenance is added."""

    recommendations: list[CalendarRecommendation]
    exclusions: list[CalendarExclusion]
    candidate_context_count: int


@dataclass(frozen=True)
class _SourceItem:
    item_type: str
    item_id: str
    text: str
    deadline_status: str | None


@dataclass
class _Context:
    sort_order: int
    source: _SourceItem | None
    items: list[TemporalItem]
    conflict_from_gap: bool = False


@dataclass
class _RecommendationDraft:
    recommendation_type: CalendarRecommendationType
    readiness_status: str
    title: str
    description: str
    schedule: CalendarSchedule
    source_temporal_ids: list[str]
    related_intelligence_items: list[CalendarIntelligenceReference]
    evidence: list[CalendarEvidenceReference]
    review_reasons: list[str]
    blocking_reasons: list[str]
    informational_flags: list[str]
    deduplication_key_sha256: str
    merged_source_count: int
    source_type: str
    source_id: str
    source_text: str
    temporal_expressions: list[str]
    fingerprint_payload: dict[str, object]


@dataclass(frozen=True)
class _ExclusionDraft:
    sort_order: int
    source_temporal_ids: list[str]
    reason: CalendarExclusionReason
    description: str
    evidence: list[CalendarEvidenceReference]


@dataclass(frozen=True)
class _ScheduleResult:
    schedule: CalendarSchedule
    review_reasons: list[str]
    blocking_reasons: list[str]
    informational_flags: list[str]


def build_calendar_recommendations(
    intelligence: DecisionIntelligenceArtifact,
    temporal: TemporalIntelligenceArtifact,
) -> CalendarPolicyResult:
    """Project trusted Phase 6 and Phase 7 artifacts into calendar recommendations."""

    source_index = _source_index(intelligence)
    _validate_links(temporal, source_index)
    temporal_order = {
        item.temporal_id: index for index, item in enumerate(temporal.items)
    }
    contexts, exclusions = _build_contexts(temporal, source_index, temporal_order)
    recommendations: list[_RecommendationDraft] = []
    for context in contexts:
        draft = _build_recommendation(context, temporal)
        if isinstance(draft, _ExclusionDraft):
            exclusions.append(draft)
        else:
            recommendations.append(draft)

    deduplicated = _deduplicate(recommendations)
    final_recommendations = [
        CalendarRecommendation(
            recommendation_id=f"calendar_rec_{index:03d}",
            recommendation_type=draft.recommendation_type,
            readiness_status=draft.readiness_status,
            title=draft.title,
            description=draft.description,
            schedule=draft.schedule,
            source_temporal_ids=draft.source_temporal_ids,
            related_intelligence_items=draft.related_intelligence_items,
            evidence=draft.evidence,
            review_reasons=draft.review_reasons,
            blocking_reasons=draft.blocking_reasons,
            informational_flags=draft.informational_flags,
            deduplication_key_sha256=draft.deduplication_key_sha256,
            merged_source_count=draft.merged_source_count,
        )
        for index, draft in enumerate(deduplicated, start=1)
    ]
    final_exclusions = [
        CalendarExclusion(
            exclusion_id=f"calendar_exclusion_{index:03d}",
            source_temporal_ids=draft.source_temporal_ids,
            reason=draft.reason,
            description=draft.description,
            evidence=draft.evidence,
        )
        for index, draft in enumerate(
            sorted(exclusions, key=lambda item: item.sort_order),
            start=1,
        )
    ]
    return CalendarPolicyResult(
        recommendations=final_recommendations,
        exclusions=final_exclusions,
        candidate_context_count=len(contexts),
    )


def _source_index(
    intelligence: DecisionIntelligenceArtifact,
) -> dict[tuple[str, str], _SourceItem]:
    index: dict[tuple[str, str], _SourceItem] = {}
    for item_type, collection in [
        ("decision", intelligence.decisions),
        ("action_item", intelligence.action_items),
        ("commitment", intelligence.commitments),
        ("follow_up", intelligence.follow_ups),
        ("missing_information", intelligence.missing_information),
        ("gap", intelligence.gaps),
    ]:
        id_field, text_field = SOURCE_TEXT_FIELDS[item_type]
        for item in collection:
            item_id = getattr(item, id_field)
            deadline = getattr(item, "deadline", None)
            index[(item_type, item_id)] = _SourceItem(
                item_type=item_type,
                item_id=item_id,
                text=getattr(item, text_field),
                deadline_status=None if deadline is None else deadline.status,
            )
    return index


def _validate_links(
    temporal: TemporalIntelligenceArtifact,
    source_index: dict[tuple[str, str], _SourceItem],
) -> None:
    seen_ids: set[str] = set()
    for item in temporal.items:
        if item.temporal_id in seen_ids:
            raise CalendarPolicyError("Temporal IDs must be unique.")
        seen_ids.add(item.temporal_id)
        seen_refs: set[tuple[str, str]] = set()
        for reference in item.related_intelligence_items:
            key = (reference.item_type, reference.item_id)
            if key in seen_refs:
                raise CalendarPolicyError("Temporal item contains duplicate links.")
            seen_refs.add(key)
            if key not in source_index:
                raise CalendarPolicyError("Temporal item references unknown intelligence.")


def _build_contexts(
    temporal: TemporalIntelligenceArtifact,
    source_index: dict[tuple[str, str], _SourceItem],
    temporal_order: dict[str, int],
) -> tuple[list[_Context], list[_ExclusionDraft]]:
    related_groups: dict[tuple[str, str], _Context] = {}
    standalone_contexts: list[_Context] = []
    reminder_contexts: list[_Context] = []
    exclusions: list[_ExclusionDraft] = []

    for item in temporal.items:
        sort_order = temporal_order[item.temporal_id]
        if item.category == "other_temporal":
            exclusions.append(
                _exclusion(
                    sort_order,
                    [item],
                    "unsupported_temporal_category",
                    "Unsupported temporal category is not calendar-actionable.",
                )
            )
            continue
        if not item.related_intelligence_items:
            if item.category == "duration":
                exclusions.append(
                    _exclusion(
                        sort_order,
                        [item],
                        "standalone_duration",
                        "Duration has no explicitly related event candidate.",
                    )
                )
            else:
                standalone_contexts.append(
                    _Context(sort_order=sort_order, source=None, items=[item])
                )
            continue

        for reference in item.related_intelligence_items:
            key = (reference.item_type, reference.item_id)
            source = source_index[key]
            if item.category == "reminder_request":
                reminder_contexts.append(
                    _Context(sort_order=sort_order, source=source, items=[item])
                )
                continue
            context = related_groups.get(key)
            if context is None:
                context = _Context(sort_order=sort_order, source=source, items=[])
                related_groups[key] = context
            context.sort_order = min(context.sort_order, sort_order)
            context.items.append(item)

    for gap in temporal.gaps:
        if gap.kind != "conflicting_temporal_information":
            continue
        if gap.related_intelligence_item is not None:
            key = (
                gap.related_intelligence_item.item_type,
                gap.related_intelligence_item.item_id,
            )
            if key in related_groups:
                related_groups[key].conflict_from_gap = True

    contexts = list(related_groups.values()) + standalone_contexts + reminder_contexts
    contexts.sort(key=lambda context: context.sort_order)
    return contexts, exclusions


def _build_recommendation(
    context: _Context,
    temporal: TemporalIntelligenceArtifact,
) -> _RecommendationDraft | _ExclusionDraft:
    if all(item.category == "duration" for item in context.items):
        return _exclusion(
            context.sort_order,
            context.items,
            "standalone_duration",
            "Duration has no explicitly related event candidate.",
        )

    recommendation_type = _classify(context)
    if recommendation_type is None:
        return _exclusion(
            context.sort_order,
            context.items,
            "insufficient_calendar_semantics",
            "Temporal reference does not contain enough calendar semantics.",
        )

    schedule_result = _build_schedule(recommendation_type, context, temporal)
    review_reasons = list(schedule_result.review_reasons)
    blocking_reasons = list(schedule_result.blocking_reasons)
    flags = list(schedule_result.informational_flags)
    if context.source is None:
        review_reasons.append("no_related_intelligence")
        flags.append("standalone_temporal_candidate")
    if any(item.resolution_status == "ambiguous" for item in context.items):
        review_reasons.append("ambiguous_temporal")
        review_reasons.append("partial_temporal_information")
    if any(item.resolution_status == "unresolved" for item in context.items):
        review_reasons.append("unresolved_temporal")
        review_reasons.append("partial_temporal_information")
    evidence = _merge_evidence(item.evidence for item in context.items)
    if len(evidence) > 1:
        flags.append("multiple_evidence_segments")
    if _has_duplicate_temporal_sources(context.items):
        flags.append("duplicate_sources_merged")

    review_reasons = _ordered_unique(review_reasons, REVIEW_REASON_ORDER)
    blocking_reasons = _ordered_unique(blocking_reasons, BLOCKING_REASON_ORDER)
    flags = _ordered_unique(flags, FLAG_ORDER)
    if blocking_reasons:
        readiness = "blocked"
        review_reasons = []
    elif review_reasons:
        readiness = "needs_review"
    else:
        readiness = "ready"

    source_type, source_id, source_text = _source_identity(context)
    expressions = _ordered_unique(
        [item.expression_text for item in context.items],
        [item.expression_text for item in context.items],
    )
    title = _title(recommendation_type, source_text)
    description = _description(source_type, source_id, source_text, expressions)
    related = (
        []
        if context.source is None
        else [
            CalendarIntelligenceReference(
                item_type=context.source.item_type,
                item_id=context.source.item_id,
            )
        ]
    )
    source_temporal_ids = _ordered_unique(
        [item.temporal_id for item in context.items],
        [item.temporal_id for item in context.items],
    )
    fingerprint_payload = _fingerprint_payload(
        recommendation_type,
        schedule_result.schedule,
        related,
        None if context.source is not None else source_text,
    )
    deduplication_key = _sha256_json(fingerprint_payload)
    return _RecommendationDraft(
        recommendation_type=recommendation_type,
        readiness_status=readiness,
        title=title,
        description=description,
        schedule=schedule_result.schedule,
        source_temporal_ids=source_temporal_ids,
        related_intelligence_items=related,
        evidence=evidence,
        review_reasons=review_reasons,
        blocking_reasons=blocking_reasons,
        informational_flags=flags,
        deduplication_key_sha256=deduplication_key,
        merged_source_count=len([item.temporal_id for item in context.items]),
        source_type=source_type,
        source_id=source_id,
        source_text=source_text,
        temporal_expressions=expressions,
        fingerprint_payload=fingerprint_payload,
    )


def _classify(context: _Context) -> CalendarRecommendationType | None:
    categories = [item.category for item in context.items]
    if "reminder_request" in categories:
        return "reminder_request"
    if "recurrence" in categories:
        return "recurring_event"
    if "milestone" in categories:
        return "milestone"
    if "deadline" in categories:
        return "deadline"
    if (
        context.source is not None
        and context.source.item_type in {"action_item", "commitment", "follow_up"}
        and any(category in {"date_reference", "datetime_reference"} for category in categories)
        and context.source.deadline_status in {"explicit", "ambiguous"}
    ):
        return "deadline"
    if any(
        category in {"time_window", "datetime_reference", "date_reference", "time_reference"}
        for category in categories
    ):
        return "event"
    return None


def _build_schedule(
    recommendation_type: CalendarRecommendationType,
    context: _Context,
    temporal: TemporalIntelligenceArtifact,
) -> _ScheduleResult:
    conflict_reasons = _detect_conflicts(context)
    if conflict_reasons:
        return _ScheduleResult(
            schedule=_unscheduled(),
            review_reasons=[],
            blocking_reasons=conflict_reasons,
            informational_flags=[],
        )

    if recommendation_type == "recurring_event":
        return _recurring_schedule(context)
    if recommendation_type == "reminder_request":
        return _reminder_schedule(context)
    if recommendation_type in {"deadline", "milestone"}:
        return _point_schedule(recommendation_type, context)
    return _event_schedule(context)


def _detect_conflicts(context: _Context) -> list[str]:
    reasons: list[str] = []
    if context.conflict_from_gap:
        reasons.append("conflicting_temporal_information")

    resolved = [
        item
        for item in context.items
        if item.resolution_status in RESOLVED_STATUSES
        and item.category not in {"duration", "recurrence"}
    ]
    if _distinct([item.start_date for item in resolved]):
        reasons.append("multiple_distinct_schedules")
    if _distinct([item.start_time for item in resolved]):
        reasons.append("multiple_distinct_schedules")
    if _distinct(
        [
            (item.end_date, item.end_time)
            for item in resolved
            if item.end_date is not None or item.end_time is not None
        ]
    ):
        reasons.append("multiple_distinct_schedules")
    if _distinct([item.timezone_name for item in resolved]):
        reasons.append("incompatible_temporal_components")
    windows = [
        (item.start_date, item.start_time, item.end_date, item.end_time)
        for item in context.items
        if item.category == "time_window"
        and item.resolution_status in RESOLVED_STATUSES
    ]
    if _distinct(windows):
        reasons.append("incompatible_temporal_components")
    durations = [
        item.duration_seconds
        for item in context.items
        if item.category == "duration" and item.duration_seconds is not None
    ]
    if _distinct(durations):
        reasons.append("incompatible_temporal_components")

    start_date = _first_value([item.start_date for item in resolved])
    start_time = _first_value([item.start_time for item in resolved])
    end_date = _first_value([item.end_date for item in resolved])
    end_time = _first_value([item.end_time for item in resolved])
    timezone_name = _first_value([item.timezone_name for item in resolved])
    duration_seconds = _first_value(durations)
    if (
        start_date
        and start_time
        and end_date
        and end_time
        and timezone_name
        and duration_seconds
    ):
        derived_end_date, derived_end_time, _ = _derive_end(
            start_date,
            start_time,
            timezone_name,
            duration_seconds,
        )
        if (derived_end_date, derived_end_time) != (end_date, end_time):
            reasons.append("incompatible_temporal_components")

    return _ordered_unique(reasons, BLOCKING_REASON_ORDER)


def _event_schedule(context: _Context) -> _ScheduleResult:
    items = context.items
    review: list[str] = []
    flags: list[str] = []
    start_date = _first_value([item.start_date for item in items])
    start_time = _first_value([item.start_time for item in items])
    end_date = _first_value([item.end_date for item in items])
    end_time = _first_value([item.end_time for item in items])
    timezone_name = _first_value([item.timezone_name for item in items])
    start_utc = _first_value([item.start_datetime_utc for item in items])
    end_utc = _first_value([item.end_datetime_utc for item in items])
    duration_seconds = _first_value(
        [
            item.duration_seconds
            for item in items
            if item.category == "duration" and item.duration_seconds is not None
        ]
    )

    if start_date and start_time and not (end_date and end_time) and duration_seconds:
        end_date, end_time, end_utc = _derive_end(
            start_date,
            start_time,
            timezone_name,
            duration_seconds,
        )
        flags.append("end_derived_from_duration")
    if start_date and start_time and end_date and end_time:
        if timezone_name is None:
            review.append("missing_timezone")
        duration_minutes = (
            int(duration_seconds // 60)
            if duration_seconds is not None and duration_seconds % 60 == 0
            else None
        )
        return _ScheduleResult(
            schedule=CalendarSchedule(
                shape="timed",
                all_day=False,
                start_date=start_date,
                start_time=start_time,
                end_date=end_date,
                end_time=end_time,
                timezone_name=timezone_name,
                start_datetime_utc=start_utc,
                end_datetime_utc=end_utc,
                duration_minutes=duration_minutes,
                recurrence=None,
                reminder_expression_text=None,
            ),
            review_reasons=review,
            blocking_reasons=[],
            informational_flags=flags,
        )
    if start_date and not start_time and end_date and not end_time:
        flags.append("date_only_candidate")
        return _ScheduleResult(
            schedule=CalendarSchedule(
                shape="all_day",
                all_day=True,
                start_date=start_date,
                start_time=None,
                end_date=end_date,
                end_time=None,
                timezone_name=timezone_name,
                start_datetime_utc=None,
                end_datetime_utc=None,
                duration_minutes=None,
                recurrence=None,
                reminder_expression_text=None,
            ),
            review_reasons=[],
            blocking_reasons=[],
            informational_flags=flags,
        )

    if not start_date:
        review.append("missing_start_date")
    if not start_time:
        review.append("missing_start_time")
    if not (end_date and end_time):
        review.append("missing_end")
    return _ScheduleResult(
        schedule=_unscheduled(),
        review_reasons=review,
        blocking_reasons=[],
        informational_flags=flags,
    )


def _point_schedule(
    recommendation_type: CalendarRecommendationType,
    context: _Context,
) -> _ScheduleResult:
    items = context.items
    review: list[str] = []
    flags: list[str] = []
    start_date = _first_value([item.start_date for item in items])
    start_time = _first_value([item.start_time for item in items])
    timezone_name = _first_value([item.timezone_name for item in items])
    start_utc = _first_value([item.start_datetime_utc for item in items])
    if not start_date:
        review.append("missing_start_date")
        return _ScheduleResult(
            schedule=_unscheduled(),
            review_reasons=review,
            blocking_reasons=[],
            informational_flags=flags,
        )
    if start_time is None:
        flags.append("date_only_candidate")
        return _ScheduleResult(
            schedule=CalendarSchedule(
                shape="all_day",
                all_day=True,
                start_date=start_date,
                start_time=None,
                end_date=None,
                end_time=None,
                timezone_name=timezone_name,
                start_datetime_utc=None,
                end_datetime_utc=None,
                duration_minutes=None,
                recurrence=None,
                reminder_expression_text=None,
            ),
            review_reasons=[],
            blocking_reasons=[],
            informational_flags=flags,
        )
    if timezone_name is None:
        review.append("missing_timezone")
    return _ScheduleResult(
        schedule=CalendarSchedule(
            shape="point_in_time",
            all_day=False,
            start_date=start_date,
            start_time=start_time,
            end_date=None,
            end_time=None,
            timezone_name=timezone_name,
            start_datetime_utc=start_utc,
            end_datetime_utc=None,
            duration_minutes=None,
            recurrence=None,
            reminder_expression_text=None,
        ),
        review_reasons=review,
        blocking_reasons=[],
        informational_flags=flags,
    )


def _recurring_schedule(context: _Context) -> _ScheduleResult:
    item = next((item for item in context.items if item.category == "recurrence"), None)
    if item is None:
        raise CalendarPolicyError("recurring candidate is missing recurrence source.")
    review: list[str] = []
    if not item.start_date:
        review.append("recurrence_missing_anchor")
    if not item.start_time:
        review.append("recurrence_missing_time")
    if item.start_time and not item.timezone_name:
        review.append("missing_timezone")
    return _ScheduleResult(
        schedule=CalendarSchedule(
            shape="recurring",
            all_day=False,
            start_date=item.start_date,
            start_time=item.start_time,
            end_date=item.end_date,
            end_time=item.end_time,
            timezone_name=item.timezone_name,
            start_datetime_utc=item.start_datetime_utc,
            end_datetime_utc=item.end_datetime_utc,
            duration_minutes=(
                int(item.duration_seconds // 60)
                if item.duration_seconds is not None and item.duration_seconds % 60 == 0
                else None
            ),
            recurrence=CalendarRecurrence(
                frequency=item.recurrence_frequency,
                interval=item.recurrence_interval,
                days=item.recurrence_days,
            ),
            reminder_expression_text=None,
        ),
        review_reasons=review,
        blocking_reasons=[],
        informational_flags=[],
    )


def _reminder_schedule(context: _Context) -> _ScheduleResult:
    item = context.items[0]
    review: list[str] = []
    if item.resolution_status == "unresolved" or item.start_date is None:
        review.append("reminder_trigger_unresolved")
        return _ScheduleResult(
            schedule=CalendarSchedule(
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
                reminder_expression_text=item.expression_text,
            ),
            review_reasons=review,
            blocking_reasons=[],
            informational_flags=[],
        )
    if item.start_time is None:
        return _ScheduleResult(
            schedule=CalendarSchedule(
                shape="all_day",
                all_day=True,
                start_date=item.start_date,
                start_time=None,
                end_date=None,
                end_time=None,
                timezone_name=item.timezone_name,
                start_datetime_utc=None,
                end_datetime_utc=None,
                duration_minutes=None,
                recurrence=None,
                reminder_expression_text=item.expression_text,
            ),
            review_reasons=[],
            blocking_reasons=[],
            informational_flags=["date_only_candidate"],
        )
    if item.timezone_name is None:
        review.append("missing_timezone")
    return _ScheduleResult(
        schedule=CalendarSchedule(
            shape="point_in_time",
            all_day=False,
            start_date=item.start_date,
            start_time=item.start_time,
            end_date=None,
            end_time=None,
            timezone_name=item.timezone_name,
            start_datetime_utc=item.start_datetime_utc,
            end_datetime_utc=None,
            duration_minutes=None,
            recurrence=None,
            reminder_expression_text=item.expression_text,
        ),
        review_reasons=review,
        blocking_reasons=[],
        informational_flags=[],
    )


def _derive_end(
    start_date: str,
    start_time: str,
    timezone_name: str | None,
    duration_seconds: int,
) -> tuple[str, str, datetime | None]:
    if duration_seconds <= 0:
        raise CalendarScheduleError("Duration must be positive.")
    start = datetime.combine(
        date.fromisoformat(start_date),
        time.fromisoformat(start_time),
    )
    end = start + timedelta(seconds=duration_seconds)
    if timezone_name is None:
        return end.date().isoformat(), _format_time(end.time()), None
    try:
        local_end = end.replace(tzinfo=ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as exc:
        raise CalendarScheduleError("Timezone is invalid for derived end.") from exc
    return (
        end.date().isoformat(),
        _format_time(end.time()),
        local_end.astimezone(timezone.utc),
    )


def _deduplicate(drafts: list[_RecommendationDraft]) -> list[_RecommendationDraft]:
    by_key: dict[str, _RecommendationDraft] = {}
    ordered_keys: list[str] = []
    for draft in drafts:
        key = draft.deduplication_key_sha256
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = draft
            ordered_keys.append(key)
            continue
        by_key[key] = _merge_draft(existing, draft)
    return [by_key[key] for key in ordered_keys]


def _merge_draft(
    first: _RecommendationDraft,
    second: _RecommendationDraft,
) -> _RecommendationDraft:
    source_temporal_ids = _ordered_unique(
        first.source_temporal_ids + second.source_temporal_ids,
        first.source_temporal_ids + second.source_temporal_ids,
    )
    evidence = _merge_calendar_evidence(first.evidence + second.evidence)
    related = _merge_related(
        first.related_intelligence_items + second.related_intelligence_items
    )
    expressions = _ordered_unique(
        first.temporal_expressions + second.temporal_expressions,
        first.temporal_expressions + second.temporal_expressions,
    )
    flags = _ordered_unique(
        first.informational_flags
        + second.informational_flags
        + ["duplicate_sources_merged"],
        FLAG_ORDER,
    )
    description = _description(
        first.source_type,
        first.source_id,
        first.source_text,
        expressions,
    )
    readiness = first.readiness_status
    review = _ordered_unique(
        first.review_reasons + second.review_reasons,
        REVIEW_REASON_ORDER,
    )
    blocking = _ordered_unique(
        first.blocking_reasons + second.blocking_reasons,
        BLOCKING_REASON_ORDER,
    )
    if blocking:
        readiness = "blocked"
        review = []
    elif review:
        readiness = "needs_review"
    return _RecommendationDraft(
        recommendation_type=first.recommendation_type,
        readiness_status=readiness,
        title=first.title,
        description=description,
        schedule=first.schedule,
        source_temporal_ids=source_temporal_ids,
        related_intelligence_items=related,
        evidence=evidence,
        review_reasons=review,
        blocking_reasons=blocking,
        informational_flags=flags,
        deduplication_key_sha256=first.deduplication_key_sha256,
        merged_source_count=first.merged_source_count + second.merged_source_count,
        source_type=first.source_type,
        source_id=first.source_id,
        source_text=first.source_text,
        temporal_expressions=expressions,
        fingerprint_payload=first.fingerprint_payload,
    )


def _source_identity(context: _Context) -> tuple[str, str, str]:
    if context.source is not None:
        return context.source.item_type, context.source.item_id, context.source.text
    expression = context.items[0].expression_text
    return "standalone", "standalone", expression


def _title(
    recommendation_type: CalendarRecommendationType,
    source_text: str,
) -> str:
    prefix = f"{RECOMMENDATION_PREFIX[recommendation_type]}: "
    normalized_source = _normalize_whitespace(source_text)
    title = prefix + normalized_source
    if len(title) <= TITLE_LIMIT:
        return title
    allowed_source_length = TITLE_LIMIT - len(prefix) - len(TITLE_ELLIPSIS)
    if allowed_source_length < 1:
        raise CalendarPolicyError("Title prefix leaves no room for source text.")
    return prefix + normalized_source[:allowed_source_length] + TITLE_ELLIPSIS


def _description(
    source_type: str,
    source_id: str,
    source_text: str,
    expressions: list[str],
) -> str:
    lines = [
        f"Source type: {source_type}",
        f"Source ID: {source_id}",
        f"Source statement: {source_text}",
    ]
    if len(expressions) == 1:
        lines.append(f"Temporal expression: {expressions[0]}")
    else:
        lines.append("Temporal expressions:")
        lines.extend(f"- {expression}" for expression in expressions)
    description = "\n".join(lines)
    if len(description) > DESCRIPTION_LIMIT:
        raise CalendarPolicyError("Calendar recommendation description is too long.")
    return description


def _fingerprint_payload(
    recommendation_type: CalendarRecommendationType,
    schedule: CalendarSchedule,
    related: list[CalendarIntelligenceReference],
    standalone_expression_text: str | None,
) -> dict[str, object]:
    return {
        "recommendation_type": recommendation_type,
        "schedule": schedule.model_dump(mode="json"),
        "related_intelligence_items": [
            item.model_dump(mode="json") for item in related
        ],
        "standalone_expression_text": standalone_expression_text,
    }


def _exclusion(
    sort_order: int,
    items: list[TemporalItem],
    reason: CalendarExclusionReason,
    description: str,
) -> _ExclusionDraft:
    return _ExclusionDraft(
        sort_order=sort_order,
        source_temporal_ids=[item.temporal_id for item in items],
        reason=reason,
        description=description,
        evidence=_merge_evidence(item.evidence for item in items),
    )


def _unscheduled() -> CalendarSchedule:
    return CalendarSchedule(
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
        reminder_expression_text=None,
    )


def _merge_evidence(
    evidence_lists: Iterable[list[TemporalEvidenceReference]],
) -> list[CalendarEvidenceReference]:
    ordered: list[CalendarEvidenceReference] = []
    seen: set[str] = set()
    for evidence in evidence_lists:
        for item in evidence:
            if item.segment_id in seen:
                continue
            seen.add(item.segment_id)
            ordered.append(
                CalendarEvidenceReference(
                    segment_id=item.segment_id,
                    speaker_label=item.speaker_label,
                    start_seconds=item.start_seconds,
                    end_seconds=item.end_seconds,
                    cleaned_text_sha256=item.cleaned_text_sha256,
                )
            )
    return ordered


def _merge_calendar_evidence(
    evidence: list[CalendarEvidenceReference],
) -> list[CalendarEvidenceReference]:
    ordered: list[CalendarEvidenceReference] = []
    seen: set[str] = set()
    for item in evidence:
        if item.segment_id in seen:
            continue
        seen.add(item.segment_id)
        ordered.append(item)
    return ordered


def _merge_related(
    references: list[CalendarIntelligenceReference],
) -> list[CalendarIntelligenceReference]:
    ordered: list[CalendarIntelligenceReference] = []
    seen: set[tuple[str, str]] = set()
    for item in references:
        key = (item.item_type, item.item_id)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def _has_duplicate_temporal_sources(items: list[TemporalItem]) -> bool:
    seen: set[str] = set()
    for item in items:
        key = json.dumps(
            {
                "category": item.category,
                "start_date": item.start_date,
                "start_time": item.start_time,
                "end_date": item.end_date,
                "end_time": item.end_time,
                "timezone_name": item.timezone_name,
                "start_datetime_utc": (
                    None
                    if item.start_datetime_utc is None
                    else item.start_datetime_utc.isoformat()
                ),
                "end_datetime_utc": (
                    None
                    if item.end_datetime_utc is None
                    else item.end_datetime_utc.isoformat()
                ),
                "duration_seconds": item.duration_seconds,
                "recurrence_frequency": item.recurrence_frequency,
                "recurrence_interval": item.recurrence_interval,
                "recurrence_days": item.recurrence_days,
                "reminder_expression_text": (
                    item.expression_text if item.category == "reminder_request" else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if key in seen:
            return True
        seen.add(key)
    return False


def _distinct(values: Iterable[object]) -> bool:
    present = [value for value in values if value not in {None, ("", "")}]
    return len({json.dumps(value, sort_keys=True, default=str) for value in present}) > 1


def _first_value(values: Iterable[object]) -> object:
    for value in values:
        if value is not None:
            return value
    return None


def _ordered_unique(values: list[str], order: list[str]) -> list[str]:
    seen = set()
    unique = []
    order_index = {value: index for index, value in enumerate(order)}
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return sorted(unique, key=lambda value: order_index.get(value, len(order_index)))


def _sha256_json(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _format_time(value: time) -> str:
    if value.second or value.microsecond:
        return value.replace(microsecond=0).isoformat()
    return value.strftime("%H:%M")
