"""Official Google Calendar API gateway for Phase 9 create-only sync."""

from __future__ import annotations

from typing import Any

from google.auth.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.app.models.google_calendar_sync import GoogleCalendarRemoteEvent
from backend.app.services.google_calendar.errors import (
    GoogleCalendarAuthenticationError,
    GoogleCalendarCalendarNotFoundError,
    GoogleCalendarConnectionError,
    GoogleCalendarPermissionError,
    GoogleCalendarProviderError,
    GoogleCalendarRateLimitError,
    GoogleCalendarRemoteConflictError,
    GoogleCalendarTimeoutError,
)


class GoogleCalendarApiGateway:
    """Small safe wrapper over the official Calendar v3 client."""

    def __init__(self, credentials: Credentials) -> None:
        self._service = build(
            "calendar",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
    ) -> GoogleCalendarRemoteEvent | None:
        try:
            response = (
                self._service.events()
                .get(calendarId=calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            if _status_code(exc) == 404:
                return None
            raise _map_http_error(exc) from exc
        except TimeoutError as exc:
            raise GoogleCalendarTimeoutError("Google Calendar request timed out.") from exc
        except OSError as exc:
            raise GoogleCalendarConnectionError(
                "Google Calendar network connection failed."
            ) from exc
        except Exception as exc:
            raise GoogleCalendarProviderError(
                "Google Calendar returned an unexpected response."
            ) from exc
        return _remote_event_from_response(response)

    def insert_event(
        self,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
    ) -> GoogleCalendarRemoteEvent:
        if body.get("id") != event_id:
            raise GoogleCalendarProviderError(
                "Google Calendar event body does not contain the deterministic ID."
            )
        try:
            response = (
                self._service.events()
                .insert(calendarId=calendar_id, body=body, sendUpdates="none")
                .execute()
            )
        except HttpError as exc:
            raise _map_http_error(exc) from exc
        except TimeoutError as exc:
            raise GoogleCalendarTimeoutError("Google Calendar request timed out.") from exc
        except OSError as exc:
            raise GoogleCalendarConnectionError(
                "Google Calendar network connection failed."
            ) from exc
        except Exception as exc:
            raise GoogleCalendarProviderError(
                "Google Calendar returned an unexpected response."
            ) from exc

        remote_event = _remote_event_from_response(response)
        if remote_event.event_id != event_id:
            raise GoogleCalendarProviderError(
                "Google Calendar response did not return the deterministic event ID."
            )
        return remote_event


def _remote_event_from_response(response: dict[str, Any]) -> GoogleCalendarRemoteEvent:
    extended = response.get("extendedProperties")
    private_properties: dict[str, str] = {}
    if isinstance(extended, dict):
        private = extended.get("private")
        if isinstance(private, dict):
            private_properties = {
                str(key): str(value) for key, value in private.items()
            }
    recurrence = response.get("recurrence")
    return GoogleCalendarRemoteEvent(
        event_id=str(response.get("id", "")),
        html_link=_safe_optional_string(response.get("htmlLink")),
        status=_safe_optional_string(response.get("status")),
        summary=_safe_optional_string(response.get("summary")),
        start=response.get("start") if isinstance(response.get("start"), dict) else None,
        end=response.get("end") if isinstance(response.get("end"), dict) else None,
        recurrence=recurrence if isinstance(recurrence, list) else None,
        private_extended_properties=private_properties,
    )


def _safe_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _status_code(exc: HttpError) -> int:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


def _map_http_error(exc: HttpError) -> Exception:
    status = _status_code(exc)
    if status == 401:
        return GoogleCalendarAuthenticationError("Google Calendar authentication failed.")
    if status == 403:
        return GoogleCalendarPermissionError("Google Calendar permission denied.")
    if status == 404:
        return GoogleCalendarCalendarNotFoundError("Google Calendar was not found.")
    if status == 409:
        return GoogleCalendarRemoteConflictError(
            "Google Calendar deterministic event ID already exists."
        )
    if status == 408:
        return GoogleCalendarTimeoutError("Google Calendar request timed out.")
    if status == 429:
        return GoogleCalendarRateLimitError("Google Calendar rate limit exceeded.")
    if status >= 500:
        return GoogleCalendarProviderError("Google Calendar provider error.")
    return GoogleCalendarProviderError("Google Calendar request failed.")
