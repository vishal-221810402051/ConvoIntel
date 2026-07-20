"""OAuth credential handling for Google Calendar sync."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from backend.app.config import Settings
from backend.app.services.google_calendar.errors import (
    GoogleCalendarAuthorizationRequiredError,
    GoogleCalendarClientSecretInvalidError,
    GoogleCalendarClientSecretMissingError,
    GoogleCalendarTokenInvalidError,
    GoogleCalendarTokenRefreshError,
)
from backend.app.services.google_calendar.scopes import GOOGLE_CALENDAR_SCOPES


def require_google_calendar_client_secret_path(settings: Settings) -> Path:
    """Return a validated OAuth client-secret JSON path."""

    path = settings.google_calendar_client_secret_path
    if path is None:
        raise GoogleCalendarClientSecretMissingError(
            "Google Calendar OAuth client-secret path is not configured."
        )
    if not path.exists() or not path.is_file():
        raise GoogleCalendarClientSecretMissingError(
            "Google Calendar OAuth client-secret JSON was not found."
        )
    if path.suffix.lower() != ".json":
        raise GoogleCalendarClientSecretInvalidError(
            "Google Calendar OAuth client-secret path must point to a JSON file."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoogleCalendarClientSecretInvalidError(
            "Google Calendar OAuth client-secret JSON is invalid."
        ) from exc
    if not isinstance(payload, dict) or not (
        isinstance(payload.get("installed"), dict)
        or isinstance(payload.get("web"), dict)
    ):
        raise GoogleCalendarClientSecretInvalidError(
            "Google Calendar OAuth client-secret JSON must contain OAuth client data."
        )
    return path.resolve(strict=False)


def load_google_calendar_credentials(settings: Settings) -> Credentials:
    """Load and refresh authorized Google Calendar credentials without browser work."""

    token_path = _token_path(settings)
    if not token_path.exists() or not token_path.is_file():
        raise GoogleCalendarAuthorizationRequiredError(
            "Google Calendar authorization is required before sync."
        )
    _validate_token_file_scopes(token_path)
    try:
        credentials = Credentials.from_authorized_user_file(
            str(token_path),
            scopes=list(GOOGLE_CALENDAR_SCOPES),
        )
    except (OSError, ValueError, GoogleAuthError) as exc:
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar authorized-user token is invalid."
        ) from exc

    _validate_scopes(credentials)
    if credentials.expired:
        if not credentials.refresh_token:
            raise GoogleCalendarAuthorizationRequiredError(
                "Google Calendar credentials are expired and cannot refresh."
            )
        try:
            credentials.refresh(Request())
        except (RefreshError, GoogleAuthError, OSError) as exc:
            raise GoogleCalendarTokenRefreshError(
                "Google Calendar credentials could not be refreshed."
            ) from exc
        _persist_authorized_credentials(token_path, credentials)

    if not credentials.valid:
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar authorized-user token is not valid."
        )
    return credentials


def save_google_calendar_credentials(
    settings: Settings,
    credentials: Credentials,
) -> Path:
    """Persist authorized-user credentials atomically to the configured token path."""

    token_path = _token_path(settings)
    _validate_scopes(credentials)
    _persist_authorized_credentials(token_path, credentials)
    return token_path.resolve(strict=False)


def _token_path(settings: Settings) -> Path:
    path = settings.google_calendar_token_path
    if path is None:
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar token path is not configured."
        )
    return path.resolve(strict=False)


def _validate_scopes(credentials: Credentials) -> None:
    scopes = tuple(credentials.scopes or credentials.granted_scopes or ())
    if scopes != GOOGLE_CALENDAR_SCOPES:
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar authorized-user token has unexpected scopes."
        )


def _validate_token_file_scopes(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar authorized-user token is invalid."
        ) from exc
    scopes_value = payload.get("scopes") if isinstance(payload, dict) else None
    if not isinstance(scopes_value, list):
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar authorized-user token has unexpected scopes."
        )
    scopes = tuple(str(scope) for scope in scopes_value)
    if scopes != GOOGLE_CALENDAR_SCOPES:
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar authorized-user token has unexpected scopes."
        )


def _persist_authorized_credentials(path: Path, credentials: Credentials) -> None:
    temp_path = path.with_name(f".tmp_google_calendar_token_{uuid.uuid4().hex}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = credentials.to_json()
        with temp_path.open("w", encoding="utf-8", newline="\n") as file:
            file.write(payload)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        os.replace(temp_path, path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise GoogleCalendarTokenInvalidError(
            "Google Calendar authorized-user token could not be saved."
        ) from exc
