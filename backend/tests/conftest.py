"""Shared pytest fixtures for backend tests."""

from pathlib import Path

import pytest

from backend.app.config import get_settings

CONVOINTEL_ENV_VARS = (
    "CONVOINTEL_ENV",
    "CONVOINTEL_HOST",
    "CONVOINTEL_PORT",
    "CONVOINTEL_LOG_LEVEL",
    "CONVOINTEL_DATA_DIR",
)


def pytest_configure(config: pytest.Config) -> None:
    """Ensure the repository-local generated pytest artifact parent exists."""

    Path(config.rootpath, ".test-artifacts").mkdir(exist_ok=True)


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch):
    for env_var in CONVOINTEL_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
