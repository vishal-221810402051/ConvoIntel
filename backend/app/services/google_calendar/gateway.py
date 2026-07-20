"""Protocol boundary for Google Calendar remote operations."""

from __future__ import annotations

from typing import Any, Protocol

from backend.app.models.google_calendar_sync import GoogleCalendarRemoteEvent


class GoogleCalendarGateway(Protocol):
    """Minimal create-only Google Calendar remote boundary."""

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
    ) -> GoogleCalendarRemoteEvent | None:
        """Return a safe remote event subset or None when it is absent."""

    def insert_event(
        self,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
    ) -> GoogleCalendarRemoteEvent:
        """Create a remote event using the deterministic Convointel event ID."""
