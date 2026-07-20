"""Tests for local temporal normalization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.app.services.temporal.errors import (
    TemporalConfigurationError,
    TemporalNormalizationError,
)
from backend.app.services.temporal.normalization import (
    normalize_provider_item,
    normalize_temporal_reference,
)
from backend.app.services.temporal.provider import ProviderTemporalItem


def temporal_item(**overrides: object) -> ProviderTemporalItem:
    values: dict[str, object] = {
        "expression_text": "30 July 2026 at 14:30",
        "category": "datetime_reference",
        "expression_type": "absolute",
        "resolution_status": "resolved_exact",
        "resolution_basis": "explicit_text",
        "precision": "datetime",
        "confidence": "high",
        "start_date": "2026-07-30",
        "start_time": "14:30",
        "end_date": None,
        "end_time": None,
        "timezone_name": "Europe/Paris",
        "utc_offset_minutes": None,
        "duration_value": None,
        "duration_unit": None,
        "recurrence_frequency": None,
        "recurrence_interval": None,
        "recurrence_days": [],
        "evidence_segment_ids": ["seg_001"],
        "related_intelligence_items": [],
    }
    values.update(overrides)
    return ProviderTemporalItem.model_validate(values)


def test_reference_requires_both_datetime_and_timezone() -> None:
    aware = datetime(2026, 7, 20, 10, tzinfo=timezone.utc)

    with pytest.raises(TemporalConfigurationError):
        normalize_temporal_reference(aware, None)

    with pytest.raises(TemporalConfigurationError):
        normalize_temporal_reference(None, "Europe/Paris")


def test_reference_must_be_aware_and_valid_iana_timezone() -> None:
    with pytest.raises(TemporalConfigurationError):
        normalize_temporal_reference(datetime(2026, 7, 20, 10), "Europe/Paris")

    with pytest.raises(TemporalConfigurationError):
        normalize_temporal_reference(
            datetime(2026, 7, 20, 10, tzinfo=timezone.utc),
            "Not/AZone",
        )


def test_reference_is_normalized_into_supplied_timezone() -> None:
    reference = normalize_temporal_reference(
        datetime(2026, 7, 20, 8, tzinfo=timezone.utc),
        "Europe/Paris",
    )

    assert reference is not None
    assert reference.source == "explicit_runtime"
    assert reference.reference_datetime_local.isoformat() == "2026-07-20T10:00:00+02:00"
    assert reference.reference_datetime_utc.isoformat() == "2026-07-20T08:00:00+00:00"
    assert reference.timezone_name == "Europe/Paris"
    assert reference.utc_offset_minutes == 120


@pytest.mark.parametrize(
    "timezone_name",
    ["Europe/Paris", "Asia/Kolkata", "America/New_York", "UTC"],
)
def test_reference_accepts_actual_iana_zones(timezone_name: str) -> None:
    reference = normalize_temporal_reference(
        datetime(2026, 7, 20, 8, tzinfo=timezone.utc),
        timezone_name,
    )

    assert reference is not None
    assert reference.timezone_name == timezone_name
    assert ZoneInfo(timezone_name).key == timezone_name


def test_absolute_datetime_converts_to_utc_locally() -> None:
    result = normalize_provider_item(
        temporal_item(),
        normalize_temporal_reference(
            datetime(2026, 7, 20, 10, tzinfo=timezone(timedelta(hours=2))),
            "Europe/Paris",
        ),
    )

    assert result.start_date == "2026-07-30"
    assert result.start_time == "14:30"
    assert result.start_datetime_utc is not None
    assert result.start_datetime_utc.isoformat() == "2026-07-30T12:30:00+00:00"


def test_reference_timezone_is_used_for_resolved_local_datetime() -> None:
    result = normalize_provider_item(
        temporal_item(timezone_name=None),
        normalize_temporal_reference(
            datetime(2026, 7, 20, 10, tzinfo=timezone(timedelta(hours=2))),
            "Europe/Paris",
        ),
    )

    assert result.timezone_name == "Europe/Paris"
    assert result.start_datetime_utc is not None
    assert result.start_datetime_utc.isoformat() == "2026-07-30T12:30:00+00:00"


def test_date_only_does_not_create_utc_midnight() -> None:
    result = normalize_provider_item(
        temporal_item(
            expression_text="30 July 2026",
            category="date_reference",
            precision="date",
            start_time=None,
            timezone_name=None,
        ),
        None,
    )

    assert result.start_date == "2026-07-30"
    assert result.start_time is None
    assert result.start_datetime_utc is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("start_date", "2026-02-30"),
        ("start_date", "2026-7-30"),
        ("start_time", "25:00"),
        ("start_time", "14:30+02:00"),
    ],
)
def test_invalid_iso_components_are_rejected(field_name: str, value: str) -> None:
    with pytest.raises(TemporalNormalizationError):
        normalize_provider_item(temporal_item(**{field_name: value}), None)


def test_leap_day_validation_accepts_valid_leap_year() -> None:
    result = normalize_provider_item(
        temporal_item(start_date="2028-02-29", start_time=None, timezone_name=None),
        None,
    )

    assert result.start_date == "2028-02-29"


def test_relative_resolution_requires_trusted_reference() -> None:
    with pytest.raises(TemporalNormalizationError):
        normalize_provider_item(
            temporal_item(
                expression_text="tomorrow",
                category="deadline",
                expression_type="deictic",
                resolution_status="resolved_relative",
                resolution_basis="reference_datetime",
                precision="date",
                start_date="2026-07-21",
                start_time=None,
                timezone_name="Europe/Paris",
            ),
            None,
        )


def test_unresolved_relative_without_reference_is_preserved() -> None:
    result = normalize_provider_item(
        temporal_item(
            expression_text="tomorrow",
            category="deadline",
            expression_type="deictic",
            resolution_status="unresolved",
            resolution_basis="insufficient_information",
            precision="unknown",
            start_date=None,
            start_time=None,
            timezone_name=None,
        ),
        None,
    )

    assert result.start_date is None
    assert result.start_datetime_utc is None


def test_provider_timezone_must_match_trusted_reference() -> None:
    reference = normalize_temporal_reference(
        datetime(2026, 7, 20, 10, tzinfo=timezone(timedelta(hours=2))),
        "Europe/Paris",
    )

    with pytest.raises(TemporalNormalizationError):
        normalize_provider_item(
            temporal_item(timezone_name="UTC"),
            reference,
        )


def test_numeric_utc_offset_can_convert_when_explicit() -> None:
    result = normalize_provider_item(
        temporal_item(timezone_name=None, utc_offset_minutes=120),
        None,
    )

    assert result.start_datetime_utc is not None
    assert result.start_datetime_utc.isoformat() == "2026-07-30T12:30:00+00:00"


def test_end_before_start_is_rejected() -> None:
    with pytest.raises(TemporalNormalizationError):
        normalize_provider_item(
            temporal_item(
                expression_text="09:00 to 08:00 on 30 July 2026",
                category="time_window",
                expression_type="range",
                precision="range",
                start_time="09:00",
                end_time="08:00",
            ),
            normalize_temporal_reference(
                datetime(2026, 7, 20, 10, tzinfo=timezone(timedelta(hours=2))),
                "Europe/Paris",
            ),
        )


@pytest.mark.parametrize(
    ("value", "unit", "seconds"),
    [
        (30, "seconds", 30),
        (90, "minutes", 5400),
        (2, "hours", 7200),
        (3, "days", 259200),
        (2, "weeks", 1209600),
    ],
)
def test_duration_units_convert_to_seconds(
    value: int,
    unit: str,
    seconds: int,
) -> None:
    result = normalize_provider_item(
        temporal_item(
            expression_text=f"{value} {unit}",
            category="duration",
            expression_type="duration",
            resolution_status="resolved_exact",
            resolution_basis="explicit_text",
            precision="duration",
            start_date=None,
            start_time=None,
            timezone_name=None,
            duration_value=value,
            duration_unit=unit,
        ),
        None,
    )

    assert result.duration_seconds == seconds


@pytest.mark.parametrize("unit", ["months", "years"])
def test_calendar_duration_units_do_not_fabricate_seconds(unit: str) -> None:
    result = normalize_provider_item(
        temporal_item(
            expression_text=f"2 {unit}",
            category="duration",
            expression_type="duration",
            resolution_status="resolved_exact",
            resolution_basis="explicit_text",
            precision="duration",
            start_date=None,
            start_time=None,
            timezone_name=None,
            duration_value=2,
            duration_unit=unit,
        ),
        None,
    )

    assert result.duration_seconds is None


@pytest.mark.parametrize(
    ("value", "unit"),
    [(0, "minutes"), (-1, "minutes"), (1, None), (None, "minutes"), (1, "fortnights")],
)
def test_invalid_duration_components_are_rejected(
    value: int | None,
    unit: str | None,
) -> None:
    with pytest.raises(TemporalNormalizationError):
        normalize_provider_item(
            temporal_item(
                expression_text="duration",
                category="duration",
                expression_type="duration",
                precision="duration",
                start_date=None,
                start_time=None,
                timezone_name=None,
                duration_value=value,
                duration_unit=unit,
            ),
            None,
        )


def test_weekly_recurrence_preserves_weekday_description() -> None:
    result = normalize_provider_item(
        temporal_item(
            expression_text="every Tuesday",
            category="recurrence",
            expression_type="recurring",
            resolution_status="resolved_exact",
            resolution_basis="explicit_text",
            precision="recurrence",
            start_date=None,
            start_time=None,
            timezone_name=None,
            recurrence_frequency="weekly",
            recurrence_interval=1,
            recurrence_days=["tuesday"],
        ),
        None,
    )

    assert result.recurrence_frequency == "weekly"
    assert result.recurrence_interval == 1
    assert result.recurrence_days == ["tuesday"]


def test_duplicate_recurrence_days_are_schema_rejected() -> None:
    with pytest.raises(ValidationError):
        temporal_item(
            expression_text="every Tuesday and Tuesday",
            category="recurrence",
            expression_type="recurring",
            precision="recurrence",
            start_date=None,
            start_time=None,
            timezone_name=None,
            recurrence_frequency="weekly",
            recurrence_interval=1,
            recurrence_days=["tuesday", "tuesday"],
        )


def test_dst_nonexistent_time_is_rejected() -> None:
    with pytest.raises(TemporalNormalizationError):
        normalize_provider_item(
            temporal_item(start_date="2026-03-29", start_time="02:30"),
            normalize_temporal_reference(
                datetime(2026, 3, 28, 10, tzinfo=timezone(timedelta(hours=1))),
                "Europe/Paris",
            ),
        )


def test_dst_ambiguous_time_is_rejected() -> None:
    with pytest.raises(TemporalNormalizationError):
        normalize_provider_item(
            temporal_item(start_date="2026-10-25", start_time="02:30"),
            normalize_temporal_reference(
                datetime(2026, 10, 24, 10, tzinfo=timezone(timedelta(hours=2))),
                "Europe/Paris",
            ),
        )
