"""Tests for Google Calendar API gateway mapping."""

from __future__ import annotations

from typing import Any

import pytest
from googleapiclient.errors import HttpError

from backend.app.services.google_calendar.errors import (
    GoogleCalendarRemoteConflictError,
)
from backend.app.services.google_calendar.google_gateway import GoogleCalendarApiGateway


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "test"


class _Request:
    def __init__(self, response: dict[str, Any] | None = None, error: HttpError | None = None) -> None:
        self.response = response
        self.error = error

    def execute(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class _Events:
    def __init__(self) -> None:
        self.get_error: HttpError | None = None
        self.insert_error: HttpError | None = None
        self.insert_kwargs: dict[str, Any] | None = None

    def get(self, **kwargs: Any) -> _Request:
        if self.get_error is not None:
            return _Request(error=self.get_error)
        return _Request(
            {
                "id": kwargs["eventId"],
                "status": "confirmed",
                "htmlLink": "https://calendar.google.com/event",
                "summary": "Title",
                "start": {"date": "2026-07-24"},
                "end": {"date": "2026-07-25"},
                "extendedProperties": {"private": {"k": "v"}},
            }
        )

    def insert(self, **kwargs: Any) -> _Request:
        self.insert_kwargs = kwargs
        if self.insert_error is not None:
            return _Request(error=self.insert_error)
        body = kwargs["body"]
        return _Request(
            {
                "id": body["id"],
                "status": "confirmed",
                "summary": body["summary"],
                "start": body["start"],
                "end": body["end"],
                "extendedProperties": body["extendedProperties"],
            }
        )


class _Service:
    def __init__(self) -> None:
        self.events_resource = _Events()

    def events(self) -> _Events:
        return self.events_resource


def make_gateway(service: _Service) -> GoogleCalendarApiGateway:
    gateway = GoogleCalendarApiGateway.__new__(GoogleCalendarApiGateway)
    gateway._service = service
    return gateway


def http_error(status: int) -> HttpError:
    return HttpError(_Response(status), b"{}")


def test_get_maps_not_found_to_none() -> None:
    service = _Service()
    service.events_resource.get_error = http_error(404)

    result = make_gateway(service).get_event("primary", "convointel123")

    assert result is None


def test_insert_uses_safe_create_only_options() -> None:
    service = _Service()
    body = {
        "id": "convointel123",
        "summary": "Title",
        "start": {"date": "2026-07-24"},
        "end": {"date": "2026-07-25"},
        "extendedProperties": {"private": {"k": "v"}},
    }

    result = make_gateway(service).insert_event("primary", "convointel123", body)

    assert result.event_id == "convointel123"
    assert service.events_resource.insert_kwargs == {
        "calendarId": "primary",
        "body": body,
        "sendUpdates": "none",
    }


def test_insert_conflict_is_typed() -> None:
    service = _Service()
    service.events_resource.insert_error = http_error(409)

    with pytest.raises(GoogleCalendarRemoteConflictError):
        make_gateway(service).insert_event(
            "primary",
            "convointel123",
            {"id": "convointel123"},
        )
