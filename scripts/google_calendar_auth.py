"""Authorize Google Calendar access for Convointel Phase 9 sync."""

from __future__ import annotations

from google_auth_oauthlib.flow import InstalledAppFlow

from backend.app.config import get_settings
from backend.app.services.google_calendar.auth import (
    require_google_calendar_client_secret_path,
    save_google_calendar_credentials,
)
from backend.app.services.google_calendar.scopes import GOOGLE_CALENDAR_SCOPES


def main() -> None:
    settings = get_settings()
    client_secret_path = require_google_calendar_client_secret_path(settings)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=GOOGLE_CALENDAR_SCOPES,
    )
    credentials = flow.run_local_server(port=0)
    token_path = save_google_calendar_credentials(settings, credentials)
    account = getattr(credentials, "id_token", None)
    print("GOOGLE_CALENDAR_AUTH_SUCCESS")
    print(f"scope={GOOGLE_CALENDAR_SCOPES[0]}")
    print(f"token_path={token_path}")
    if account is not None:
        print("account=authorized")


if __name__ == "__main__":
    main()
