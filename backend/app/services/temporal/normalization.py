"""Local temporal normalization helpers for Phase 7."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.app.models.temporal import TemporalReference
from backend.app.services.temporal.errors import (
    TemporalConfigurationError,
    TemporalNormalizationError,
    TemporalTimezoneError,
)
from backend.app.services.temporal.provider import ProviderTemporalItem, WEEKDAY_ORDER

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}(?::\d{2})?$")
RELATIVE_TYPES = {"relative", "deictic"}
DURATION_SECONDS_PER_UNIT = {
    "second": 1,
    "seconds": 1,
    "minute": 60,
    "minutes": 60,
    "hour": 60 * 60,
    "hours": 60 * 60,
    "day": 24 * 60 * 60,
    "days": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "weeks": 7 * 24 * 60 * 60,
}
DURATION_UNITS_WITHOUT_SECONDS = {"month", "months", "year", "years"}
RECURRENCE_FREQUENCIES = {"daily", "weekly", "monthly", "yearly"}


@dataclass(frozen=True)
class NormalizedTemporalComponents:
    start_date: str | None
    start_time: str | None
    end_date: str | None
    end_time: str | None
    timezone_name: str | None
    utc_offset_minutes: int | None
    start_datetime_utc: datetime | None
    end_datetime_utc: datetime | None
    duration_value: float | None
    duration_unit: str | None
    duration_seconds: int | None
    recurrence_frequency: str | None
    recurrence_interval: int | None
    recurrence_days: list[str]


def normalize_temporal_reference(
    reference_datetime: datetime | None,
    timezone_name: str | None,
) -> TemporalReference | None:
    """Validate and canonicalize the optional trusted runtime reference."""

    if (reference_datetime is None) != (timezone_name is None):
        raise TemporalConfigurationError(
            "reference_datetime and timezone_name must be supplied together."
        )
    if reference_datetime is None:
        return None
    if reference_datetime.tzinfo is None or reference_datetime.utcoffset() is None:
        raise TemporalConfigurationError("reference_datetime must be timezone-aware.")

    zone = _load_zone(timezone_name)
    local = reference_datetime.astimezone(zone)
    offset = local.utcoffset()
    if offset is None:
        raise TemporalConfigurationError("reference timezone offset is invalid.")
    return TemporalReference(
        reference_datetime_local=local,
        reference_datetime_utc=local.astimezone(timezone.utc),
        timezone_name=timezone_name,
        utc_offset_minutes=int(offset.total_seconds() // 60),
    )


def normalize_provider_item(
    item: ProviderTemporalItem,
    reference: TemporalReference | None,
) -> NormalizedTemporalComponents:
    """Validate provider temporal components and calculate local UTC values."""

    start_date = _parse_iso_date(item.start_date, "start_date")
    end_date = _parse_iso_date(item.end_date, "end_date")
    start_time = _parse_iso_time(item.start_time, "start_time")
    end_time = _parse_iso_time(item.end_time, "end_time")

    timezone_name = _validate_timezone_name(item.timezone_name)
    utc_offset_minutes = _validate_utc_offset(item.utc_offset_minutes)

    if (
        reference is not None
        and timezone_name is None
        and utc_offset_minutes is None
        and start_date is not None
        and start_time is not None
        and item.resolution_status in {"resolved_exact", "resolved_relative"}
    ):
        timezone_name = reference.timezone_name

    if reference is None:
        if item.expression_type in RELATIVE_TYPES and item.resolution_status in {
            "resolved_exact",
            "resolved_relative",
        }:
            raise TemporalNormalizationError(
                "relative expressions require a trusted reference to resolve."
            )
    else:
        if timezone_name is not None and timezone_name != reference.timezone_name:
            raise TemporalNormalizationError(
                "provider timezone must match the trusted reference timezone."
            )

    if item.resolution_status == "resolved_relative":
        if reference is None:
            raise TemporalNormalizationError(
                "resolved_relative requires a trusted reference."
            )
        if item.resolution_basis != "reference_datetime":
            raise TemporalNormalizationError(
                "resolved_relative requires reference_datetime basis."
            )

    if item.resolution_status in {"resolved_exact", "resolved_relative"}:
        if not _has_normalized_information(
            item,
            start_date,
            start_time,
            end_date,
            end_time,
        ):
            raise TemporalNormalizationError(
                "resolved temporal items require normalized information."
            )

    if item.resolution_status == "unresolved":
        if start_date or start_time or end_date or end_time:
            raise TemporalNormalizationError(
                "unresolved temporal items must not contain invented date/time values."
            )

    effective_end_date = end_date
    if effective_end_date is None and end_time is not None and start_date is not None:
        effective_end_date = start_date

    _validate_range(start_date, start_time, effective_end_date, end_time)

    duration_value, duration_unit, duration_seconds = _normalize_duration(
        item.duration_value,
        item.duration_unit,
    )
    recurrence_frequency, recurrence_interval, recurrence_days = _normalize_recurrence(
        item.recurrence_frequency,
        item.recurrence_interval,
        item.recurrence_days,
    )

    start_datetime_utc = _to_utc(
        local_date=start_date,
        local_time=start_time,
        timezone_name=timezone_name,
        utc_offset_minutes=utc_offset_minutes,
    )
    end_datetime_utc = _to_utc(
        local_date=effective_end_date,
        local_time=end_time,
        timezone_name=timezone_name,
        utc_offset_minutes=utc_offset_minutes,
    )

    return NormalizedTemporalComponents(
        start_date=start_date.isoformat() if start_date else None,
        start_time=_format_time(start_time),
        end_date=effective_end_date.isoformat() if effective_end_date else None,
        end_time=_format_time(end_time),
        timezone_name=timezone_name,
        utc_offset_minutes=utc_offset_minutes,
        start_datetime_utc=start_datetime_utc,
        end_datetime_utc=end_datetime_utc,
        duration_value=duration_value,
        duration_unit=duration_unit,
        duration_seconds=duration_seconds,
        recurrence_frequency=recurrence_frequency,
        recurrence_interval=recurrence_interval,
        recurrence_days=recurrence_days,
    )


def _load_zone(timezone_name: str | None) -> ZoneInfo:
    if timezone_name is None or not timezone_name.strip():
        raise TemporalConfigurationError("timezone_name must be a valid IANA timezone.")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise TemporalTimezoneError(
            f"Unsupported or unavailable IANA timezone: {timezone_name}"
        ) from exc


def _validate_timezone_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise TemporalNormalizationError("timezone_name must not be empty.")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise TemporalTimezoneError(
            f"Unsupported or unavailable IANA timezone: {normalized}"
        ) from exc
    return normalized


def _validate_utc_offset(value: int | None) -> int | None:
    if value is None:
        return None
    if value < -14 * 60 or value > 14 * 60:
        raise TemporalNormalizationError("utc_offset_minutes is outside valid range.")
    return value


def _parse_iso_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    if not DATE_PATTERN.fullmatch(value):
        raise TemporalNormalizationError(f"{field_name} must be YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise TemporalNormalizationError(f"{field_name} is not a valid date.") from exc


def _parse_iso_time(value: str | None, field_name: str) -> time | None:
    if value is None:
        return None
    if not TIME_PATTERN.fullmatch(value):
        raise TemporalNormalizationError(
            f"{field_name} must be HH:MM or HH:MM:SS without timezone."
        )
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise TemporalNormalizationError(f"{field_name} is not a valid time.") from exc
    if parsed.tzinfo is not None:
        raise TemporalNormalizationError(f"{field_name} must not include timezone.")
    return parsed


def _has_normalized_information(
    item: ProviderTemporalItem,
    start_date: date | None,
    start_time: time | None,
    end_date: date | None,
    end_time: time | None,
) -> bool:
    if start_date or start_time or end_date or end_time:
        return True
    if item.duration_value is not None or item.duration_unit is not None:
        return True
    if item.recurrence_frequency is not None or item.recurrence_days:
        return True
    return False


def _validate_range(
    start_date: date | None,
    start_time: time | None,
    end_date: date | None,
    end_time: time | None,
) -> None:
    if end_date is not None and start_date is None:
        raise TemporalNormalizationError("end_date requires start_date.")
    if end_time is not None and start_time is None:
        raise TemporalNormalizationError("end_time requires start_time.")
    if start_date is None or end_date is None:
        return
    if start_time is None and end_time is None:
        if end_date < start_date:
            raise TemporalNormalizationError("end_date must not be before start_date.")
        return
    if start_time is None or end_time is None:
        return
    start = datetime.combine(start_date, start_time)
    end = datetime.combine(end_date, end_time)
    if end < start:
        raise TemporalNormalizationError("temporal range end is before start.")


def _normalize_duration(
    value: float | None,
    unit: str | None,
) -> tuple[float | None, str | None, int | None]:
    if value is None and unit is None:
        return None, None, None
    if value is None or unit is None:
        raise TemporalNormalizationError("duration_value and duration_unit must match.")
    if value <= 0:
        raise TemporalNormalizationError("duration_value must be positive.")
    normalized_unit = unit.strip().casefold()
    if not normalized_unit:
        raise TemporalNormalizationError("duration_unit must not be empty.")
    if normalized_unit in DURATION_SECONDS_PER_UNIT:
        seconds = int(value * DURATION_SECONDS_PER_UNIT[normalized_unit])
        if seconds <= 0:
            raise TemporalNormalizationError("duration_seconds must be positive.")
        return value, normalized_unit, seconds
    if normalized_unit in DURATION_UNITS_WITHOUT_SECONDS:
        return value, normalized_unit, None
    raise TemporalNormalizationError("duration_unit is unsupported.")


def _normalize_recurrence(
    frequency: str | None,
    interval: int | None,
    days: list[str],
) -> tuple[str | None, int | None, list[str]]:
    if frequency is None and interval is None and not days:
        return None, None, []
    if frequency is None:
        raise TemporalNormalizationError("recurrence_frequency is required.")
    normalized_frequency = frequency.strip().casefold()
    if normalized_frequency not in RECURRENCE_FREQUENCIES:
        raise TemporalNormalizationError("recurrence_frequency is unsupported.")
    normalized_interval = interval or 1
    if normalized_interval <= 0:
        raise TemporalNormalizationError("recurrence_interval must be positive.")
    normalized_days = [day.strip().casefold() for day in days]
    if any(day not in WEEKDAY_ORDER for day in normalized_days):
        raise TemporalNormalizationError("recurrence_days must be weekdays.")
    if len(set(normalized_days)) != len(normalized_days):
        raise TemporalNormalizationError("recurrence_days must be unique.")
    if normalized_days != sorted(normalized_days, key=WEEKDAY_ORDER.__getitem__):
        raise TemporalNormalizationError("recurrence_days must be weekday ordered.")
    return normalized_frequency, normalized_interval, normalized_days


def _to_utc(
    *,
    local_date: date | None,
    local_time: time | None,
    timezone_name: str | None,
    utc_offset_minutes: int | None,
) -> datetime | None:
    if local_date is None or local_time is None:
        return None
    naive = datetime.combine(local_date, local_time)
    if timezone_name is not None:
        aware = _attach_zone_safely(naive, timezone_name)
    elif utc_offset_minutes is not None:
        aware = naive.replace(tzinfo=timezone(timedelta(minutes=utc_offset_minutes)))
    else:
        return None
    return aware.astimezone(timezone.utc)


def _attach_zone_safely(naive: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    candidates: list[datetime] = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        round_trip = aware.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == naive:
            candidates.append(aware)
    if not candidates:
        raise TemporalNormalizationError("local time does not exist in timezone.")
    offsets = {candidate.utcoffset() for candidate in candidates}
    if len(candidates) > 1 and len(offsets) > 1:
        raise TemporalNormalizationError("local time is ambiguous in timezone.")
    return candidates[0]


def _format_time(value: time | None) -> str | None:
    if value is None:
        return None
    if value.second:
        return value.isoformat(timespec="seconds")
    return value.isoformat(timespec="minutes")
