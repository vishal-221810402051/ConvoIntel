"""Pure Google Calendar event payload construction for Phase 9."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.app.models.calendar_recommendation import (
    CalendarRecommendation,
    CalendarSchedule,
)
from backend.app.models.google_calendar_sync import (
    GOOGLE_CALENDAR_SYNC_VERSION,
    SAFE_GOOGLE_EVENT_ID_PATTERN_TEXT,
)
from backend.app.services.google_calendar.errors import (
    GoogleCalendarPayloadError,
    GoogleCalendarRecommendationNotReadyError,
    GoogleCalendarRecommendationUnsupportedError,
)

GOOGLE_EVENT_ID_PREFIX = "convointel"
GOOGLE_EVENT_ID_HASH_LENGTH = 48
GOOGLE_EVENT_ID_PATTERN = re.compile(SAFE_GOOGLE_EVENT_ID_PATTERN_TEXT)
PRIVATE_PROPERTY_MEETING_ID = "convointel_meeting_id"
PRIVATE_PROPERTY_RECOMMENDATION_ID = "convointel_recommendation_id"
PRIVATE_PROPERTY_RECOMMENDATION_HASH = "convointel_recommendation_hash"
PRIVATE_PROPERTY_SYNC_VERSION = "convointel_sync_version"

_RECURRENCE_FREQUENCIES = {
    "daily": "DAILY",
    "weekly": "WEEKLY",
    "monthly": "MONTHLY",
    "yearly": "YEARLY",
}
_WEEKDAY_CODES = {
    "monday": "MO",
    "tuesday": "TU",
    "wednesday": "WE",
    "thursday": "TH",
    "friday": "FR",
    "saturday": "SA",
    "sunday": "SU",
}


@dataclass(frozen=True)
class GoogleCalendarPreparedPayload:
    """Deterministic event ID, body, and payload checksum."""

    event_id: str
    body: dict[str, Any]
    payload_sha256: str


def build_google_calendar_event_payload(
    *,
    meeting_id: str,
    calendar_id: str,
    recommendations_sha256: str,
    recommendation: CalendarRecommendation,
) -> GoogleCalendarPreparedPayload:
    """Build a create-only Google Calendar event payload."""

    _validate_syncable_recommendation(recommendation)
    event_id = deterministic_google_event_id(
        calendar_id=calendar_id,
        meeting_id=meeting_id,
        recommendation_id=recommendation.recommendation_id,
        recommendations_sha256=recommendations_sha256,
        deduplication_key_sha256=recommendation.deduplication_key_sha256,
    )
    body = _base_event_body(meeting_id, recommendation, recommendations_sha256)
    body["id"] = event_id

    schedule = recommendation.schedule
    if schedule.shape == "all_day":
        body.update(_all_day_fields(schedule))
    elif schedule.shape == "timed":
        body.update(_timed_fields(schedule))
    elif schedule.shape == "point_in_time":
        body.update(_point_in_time_fields(schedule))
    elif schedule.shape == "recurring":
        body.update(_recurring_fields(schedule))
    else:
        raise GoogleCalendarRecommendationUnsupportedError(
            "Recommendation schedule is not syncable."
        )

    return GoogleCalendarPreparedPayload(
        event_id=event_id,
        body=body,
        payload_sha256=stable_json_sha256(body),
    )


def deterministic_google_event_id(
    *,
    calendar_id: str,
    meeting_id: str,
    recommendation_id: str,
    recommendations_sha256: str,
    deduplication_key_sha256: str,
) -> str:
    """Return the deterministic Google event ID for one recommendation."""

    fingerprint = stable_json_sha256(
        {
            "sync_version": GOOGLE_CALENDAR_SYNC_VERSION,
            "calendar_id": calendar_id,
            "meeting_id": meeting_id,
            "recommendation_id": recommendation_id,
            "recommendations_sha256": recommendations_sha256,
            "deduplication_key_sha256": deduplication_key_sha256,
        }
    )
    event_id = f"{GOOGLE_EVENT_ID_PREFIX}{fingerprint[:GOOGLE_EVENT_ID_HASH_LENGTH]}"
    if GOOGLE_EVENT_ID_PATTERN.fullmatch(event_id) is None:
        raise GoogleCalendarPayloadError("Deterministic Google event ID is invalid.")
    return event_id


def stable_json_sha256(payload: dict[str, Any]) -> str:
    """Return a stable JSON SHA-256 checksum."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_syncable_recommendation(recommendation: CalendarRecommendation) -> None:
    if recommendation.readiness_status != "ready":
        raise GoogleCalendarRecommendationNotReadyError(
            "Recommendation is not ready for sync."
        )
    if recommendation.review_reasons or recommendation.blocking_reasons:
        raise GoogleCalendarRecommendationNotReadyError(
            "Recommendation contains review or blocking reasons."
        )
    if recommendation.recommendation_type == "reminder_request":
        raise GoogleCalendarRecommendationUnsupportedError(
            "Reminder recommendations are not syncable calendar events."
        )
    if recommendation.schedule.shape in {"unscheduled"}:
        raise GoogleCalendarRecommendationUnsupportedError(
            "Unscheduled recommendations are not syncable."
        )


def _base_event_body(
    meeting_id: str,
    recommendation: CalendarRecommendation,
    recommendations_sha256: str,
) -> dict[str, Any]:
    description = (
        f"{recommendation.description}\n\n"
        f"Convointel meeting ID: {meeting_id}\n"
        f"Convointel recommendation ID: {recommendation.recommendation_id}"
    )
    return {
        "summary": recommendation.title,
        "description": description,
        "reminders": {"useDefault": True},
        "extendedProperties": {
            "private": {
                PRIVATE_PROPERTY_MEETING_ID: meeting_id,
                PRIVATE_PROPERTY_RECOMMENDATION_ID: recommendation.recommendation_id,
                PRIVATE_PROPERTY_RECOMMENDATION_HASH: recommendations_sha256,
                PRIVATE_PROPERTY_SYNC_VERSION: GOOGLE_CALENDAR_SYNC_VERSION,
            }
        },
    }


def _all_day_fields(schedule: CalendarSchedule) -> dict[str, Any]:
    if not schedule.start_date:
        raise GoogleCalendarPayloadError("All-day schedules require a start date.")
    start = _parse_date(schedule.start_date, "start_date")
    if schedule.end_date and schedule.end_date != schedule.start_date:
        raise GoogleCalendarRecommendationUnsupportedError(
            "Multi-day all-day recommendations are not syncable in Phase 9."
        )
    end = start + timedelta(days=1)
    return {
        "start": {"date": start.isoformat()},
        "end": {"date": end.isoformat()},
    }


def _timed_fields(schedule: CalendarSchedule) -> dict[str, Any]:
    start, end, timezone_name = _schedule_datetimes(schedule)
    return {
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": timezone_name,
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": timezone_name,
        },
    }


def _point_in_time_fields(schedule: CalendarSchedule) -> dict[str, Any]:
    if not (schedule.end_date and schedule.end_time and schedule.end_datetime_utc):
        raise GoogleCalendarRecommendationUnsupportedError(
            "Point-in-time recommendations require an explicit end to sync."
        )
    return _timed_fields(schedule)


def _recurring_fields(schedule: CalendarSchedule) -> dict[str, Any]:
    fields = _timed_fields(schedule)
    fields["recurrence"] = [_rrule(schedule)]
    return fields


def _schedule_datetimes(
    schedule: CalendarSchedule,
) -> tuple[datetime, datetime, str]:
    if not schedule.timezone_name:
        raise GoogleCalendarPayloadError("Timed schedules require an IANA timezone.")
    if not (schedule.start_date and schedule.start_time):
        raise GoogleCalendarPayloadError("Timed schedules require a local start.")
    if not (schedule.end_date and schedule.end_time):
        raise GoogleCalendarPayloadError("Timed schedules require a local end.")
    if not (schedule.start_datetime_utc and schedule.end_datetime_utc):
        raise GoogleCalendarPayloadError("Timed schedules require trusted UTC values.")
    try:
        zone = ZoneInfo(schedule.timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise GoogleCalendarPayloadError("Timed schedule timezone is unsupported.") from exc

    start = datetime.combine(
        _parse_date(schedule.start_date, "start_date"),
        _parse_time(schedule.start_time, "start_time"),
        tzinfo=zone,
    )
    end = datetime.combine(
        _parse_date(schedule.end_date, "end_date"),
        _parse_time(schedule.end_time, "end_time"),
        tzinfo=zone,
    )
    if end <= start:
        raise GoogleCalendarPayloadError("Timed schedule end must be after start.")
    _validate_utc_mapping(start, schedule.start_datetime_utc, "start_datetime_utc")
    _validate_utc_mapping(end, schedule.end_datetime_utc, "end_datetime_utc")
    return start, end, schedule.timezone_name


def _rrule(schedule: CalendarSchedule) -> str:
    recurrence = schedule.recurrence
    if recurrence is None:
        raise GoogleCalendarPayloadError("Recurring schedules require recurrence data.")
    frequency = (recurrence.frequency or "").strip().lower()
    byday: str | None = None
    if frequency == "weekdays":
        google_frequency = "WEEKLY"
        byday = "MO,TU,WE,TH,FR"
    else:
        google_frequency = _RECURRENCE_FREQUENCIES.get(frequency)
        if google_frequency is None:
            raise GoogleCalendarRecommendationUnsupportedError(
                "Recurring recommendation frequency is unsupported."
            )
        if recurrence.days:
            if google_frequency != "WEEKLY":
                raise GoogleCalendarRecommendationUnsupportedError(
                    "Monthly and yearly weekday recurrence details are ambiguous."
                )
            byday = ",".join(_weekday_code(day) for day in recurrence.days)

    parts = [f"FREQ={google_frequency}"]
    if recurrence.interval not in (None, 1):
        parts.append(f"INTERVAL={recurrence.interval}")
    if byday:
        parts.append(f"BYDAY={byday}")
    return "RRULE:" + ";".join(parts)


def _weekday_code(value: str) -> str:
    code = _WEEKDAY_CODES.get(value.strip().lower())
    if code is None:
        raise GoogleCalendarRecommendationUnsupportedError(
            "Recurring recommendation weekday is unsupported."
        )
    return code


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise GoogleCalendarPayloadError(f"{field_name} must be an ISO date.") from exc


def _parse_time(value: str, field_name: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise GoogleCalendarPayloadError(f"{field_name} must be an ISO time.") from exc


def _validate_utc_mapping(
    local_datetime: datetime,
    trusted_utc_datetime: datetime,
    field_name: str,
) -> None:
    if trusted_utc_datetime.tzinfo is None or trusted_utc_datetime.utcoffset() is None:
        raise GoogleCalendarPayloadError(f"{field_name} must be timezone-aware.")
    expected = local_datetime.astimezone(timezone.utc)
    actual = trusted_utc_datetime.astimezone(timezone.utc)
    if expected != actual:
        raise GoogleCalendarPayloadError(
            f"{field_name} does not match the trusted local schedule."
        )
