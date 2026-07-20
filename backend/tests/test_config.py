"""Configuration and path-resolution tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.core.paths import get_repository_root
from backend.app.logging_config import HANDLER_MARKER, configure_logging


def test_default_data_dir_resolves_to_repository_data_dir() -> None:
    settings = Settings()

    assert settings.data_dir.is_absolute()
    assert settings.data_dir == (get_repository_root() / "data").resolve(strict=False)


def test_environment_variable_overrides_are_respected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("CONVOINTEL_ENV", "test")
    monkeypatch.setenv("CONVOINTEL_HOST", "0.0.0.0")
    monkeypatch.setenv("CONVOINTEL_PORT", "8877")
    monkeypatch.setenv("CONVOINTEL_LOG_LEVEL", "debug")
    monkeypatch.setenv("CONVOINTEL_DATA_DIR", str(runtime_data_dir))
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8877
    assert settings.log_level == "DEBUG"
    assert settings.data_dir == runtime_data_dir.resolve(strict=False)
    assert settings.data_dir.is_absolute()


def test_relative_data_dir_override_resolves_from_repository_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONVOINTEL_DATA_DIR", "local-data")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.data_dir == (get_repository_root() / "local-data").resolve(
        strict=False,
    )


def test_invalid_port_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONVOINTEL_PORT", "0")
    get_settings.cache_clear()

    with pytest.raises(ValidationError) as exc_info:
        get_settings()

    assert "greater than or equal to 1" in str(exc_info.value)


def test_invalid_log_level_fails_clearly() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(log_level="verbose")

    assert "CONVOINTEL_LOG_LEVEL must be one of" in str(exc_info.value)


def test_configure_logging_does_not_duplicate_handlers() -> None:
    configure_logging("INFO")
    first_count = sum(
        1
        for handler in __import__("logging").getLogger().handlers
        if getattr(handler, HANDLER_MARKER, False)
    )

    configure_logging("DEBUG")
    second_count = sum(
        1
        for handler in __import__("logging").getLogger().handlers
        if getattr(handler, HANDLER_MARKER, False)
    )

    assert first_count == 1
    assert second_count == 1
