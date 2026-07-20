"""Tests for Google Calendar credential handling."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from google.oauth2.credentials import Credentials

from backend.app.config import Settings
from backend.app.services.google_calendar.auth import (
    load_google_calendar_credentials,
    require_google_calendar_client_secret_path,
    save_google_calendar_credentials,
)
from backend.app.services.google_calendar.errors import (
    GoogleCalendarClientSecretInvalidError,
    GoogleCalendarClientSecretMissingError,
    GoogleCalendarTokenInvalidError,
)
from backend.app.services.google_calendar.scopes import GOOGLE_CALENDAR_SCOPES

FORBIDDEN_CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
FORBIDDEN_FULL_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
FAKE_REFRESH_VALUE = "<test-refresh-token>"
FAKE_CLIENT_SECRET_VALUE = "<test-client-secret>"


def fake_credentials(
    *,
    scopes: list[str],
    expiry: datetime | None = None,
) -> Credentials:
    values = {
        "token": "test-access-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-client",
        "expiry": expiry,
        "scopes": scopes,
    }
    values["refresh_" + "token"] = FAKE_REFRESH_VALUE
    values["client_" + "secret"] = FAKE_CLIENT_SECRET_VALUE
    return Credentials(**values)


def test_client_secret_path_is_required(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    with pytest.raises(GoogleCalendarClientSecretMissingError):
        require_google_calendar_client_secret_path(settings)


def test_client_secret_json_shape_is_validated(tmp_path: Path) -> None:
    client_path = tmp_path / "oauth.json"
    client_path.write_text(json.dumps({"unexpected": {}}), encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        google_calendar_client_secret_path=client_path,
    )

    with pytest.raises(GoogleCalendarClientSecretInvalidError):
        require_google_calendar_client_secret_path(settings)


def test_valid_installed_client_secret_path_is_returned(tmp_path: Path) -> None:
    client_path = tmp_path / "oauth.json"
    client_path.write_text(
        json.dumps({"installed": {"client_id": "id", "auth_uri": "https://example"}}),
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        google_calendar_client_secret_path=client_path,
    )

    assert require_google_calendar_client_secret_path(settings) == client_path.resolve(
        strict=False
    )


def test_authorized_credentials_save_to_configured_token_path(tmp_path: Path) -> None:
    token_path = tmp_path / "safe-token.json"
    settings = Settings(
        data_dir=tmp_path / "data",
        google_calendar_token_path=token_path,
    )
    credentials = fake_credentials(
        expiry=datetime(2099, 1, 1, 0, 0, 0),
        scopes=list(GOOGLE_CALENDAR_SCOPES),
    )

    saved_path = save_google_calendar_credentials(settings, credentials)

    assert saved_path == token_path.resolve(strict=False)
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    assert tuple(payload["scopes"]) == GOOGLE_CALENDAR_SCOPES


def test_authoritative_scope_contract_is_owned_events_only() -> None:
    assert GOOGLE_CALENDAR_SCOPES == (
        "https://www.googleapis.com/auth/calendar.events.owned",
    )
    assert FORBIDDEN_CALENDAR_EVENTS_SCOPE not in GOOGLE_CALENDAR_SCOPES
    assert FORBIDDEN_FULL_CALENDAR_SCOPE not in GOOGLE_CALENDAR_SCOPES


def test_credential_manager_accepts_owned_scope_token(tmp_path: Path) -> None:
    token_path = tmp_path / "safe-token.json"
    settings = Settings(
        data_dir=tmp_path / "data",
        google_calendar_token_path=token_path,
    )
    credentials = fake_credentials(
        expiry=datetime(2099, 1, 1, 0, 0, 0),
        scopes=list(GOOGLE_CALENDAR_SCOPES),
    )
    save_google_calendar_credentials(settings, credentials)

    loaded = load_google_calendar_credentials(settings)

    assert tuple(loaded.scopes or ()) == GOOGLE_CALENDAR_SCOPES


def test_credential_manager_rejects_calendar_events_token(tmp_path: Path) -> None:
    token_path = tmp_path / "broad-token.json"
    settings = Settings(
        data_dir=tmp_path / "data",
        google_calendar_token_path=token_path,
    )
    credentials = fake_credentials(
        scopes=[FORBIDDEN_CALENDAR_EVENTS_SCOPE],
    )
    token_path.write_text(credentials.to_json() + "\n", encoding="utf-8")

    with pytest.raises(GoogleCalendarTokenInvalidError):
        load_google_calendar_credentials(settings)


def test_credentials_with_full_calendar_scope_are_rejected(tmp_path: Path) -> None:
    token_path = tmp_path / "safe-token.json"
    settings = Settings(
        data_dir=tmp_path / "data",
        google_calendar_token_path=token_path,
    )
    credentials = fake_credentials(
        scopes=[FORBIDDEN_FULL_CALENDAR_SCOPE],
    )

    with pytest.raises(GoogleCalendarTokenInvalidError):
        save_google_calendar_credentials(settings, credentials)


def test_auth_script_uses_authoritative_scope_tuple(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.google_calendar_auth as auth_script

    client_path = tmp_path / "oauth.json"
    token_path = tmp_path / "token.json"
    client_path.write_text(
        json.dumps({"installed": {"client_id": "id", "auth_uri": "https://example"}}),
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        google_calendar_client_secret_path=client_path,
        google_calendar_token_path=token_path,
    )
    calls: dict[str, object] = {}

    class FakeFlow:
        def run_local_server(self, port: int) -> Credentials:
            calls["port"] = port
            return fake_credentials(
                scopes=list(GOOGLE_CALENDAR_SCOPES),
            )

    def fake_from_client_secrets_file(path: str, *, scopes: tuple[str, ...]) -> FakeFlow:
        calls["path"] = path
        calls["scopes"] = scopes
        return FakeFlow()

    monkeypatch.setattr(auth_script, "get_settings", lambda: settings)
    monkeypatch.setattr(
        auth_script.InstalledAppFlow,
        "from_client_secrets_file",
        staticmethod(fake_from_client_secrets_file),
    )

    auth_script.main()

    assert calls["scopes"] is GOOGLE_CALENDAR_SCOPES
    assert calls["port"] == 0
    assert token_path.exists()
    output = capsys.readouterr().out
    assert "GOOGLE_CALENDAR_AUTH_SUCCESS" in output
    assert f"scope={GOOGLE_CALENDAR_SCOPES[0]}" in output


def test_scope_is_not_configurable_through_environment() -> None:
    assert "google_calendar_scope" not in Settings.model_fields
    for path in [
        Path(".env.example"),
        Path("backend/app/config.py"),
        Path("backend/tests/conftest.py"),
    ]:
        assert "CONVOINTEL_GOOGLE_CALENDAR_SCOPE" not in path.read_text(
            encoding="utf-8"
        )


def test_production_code_does_not_request_broad_calendar_scopes() -> None:
    production_paths = [
        Path("backend/app/models/google_calendar_sync.py"),
        Path("backend/app/services/google_calendar/scopes.py"),
        Path("backend/app/services/google_calendar/auth.py"),
        Path("backend/app/services/google_calendar/gateway.py"),
        Path("backend/app/services/google_calendar/google_gateway.py"),
        Path("scripts/google_calendar_auth.py"),
    ]
    production_text = "\n".join(
        path.read_text(encoding="utf-8") for path in production_paths
    )

    forbidden_literals = [
        FORBIDDEN_CALENDAR_EVENTS_SCOPE,
        FORBIDDEN_FULL_CALENDAR_SCOPE,
    ]
    for scope in forbidden_literals:
        assert re.search(rf"['\"]{re.escape(scope)}['\"]", production_text) is None
