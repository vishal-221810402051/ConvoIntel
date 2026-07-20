"""Tests for the OpenAI diarized transcription provider boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import openai
import pytest

import backend.app.services.transcription.openai_provider as provider_module
from backend.app.config import Settings
from backend.app.models.transcription import (
    DurationTranscriptionUsage,
    TokenTranscriptionUsage,
)
from backend.app.services.transcription.errors import (
    TranscriptionApiKeyMissingError,
    TranscriptionAuthenticationError,
    TranscriptionConnectionError,
    TranscriptionPermissionError,
    TranscriptionProviderError,
    TranscriptionProviderResponseError,
    TranscriptionRateLimitError,
    TranscriptionRequestError,
    TranscriptionTimeoutError,
)
from backend.app.services.transcription.openai_provider import OpenAITranscriptionProvider
from backend.app.services.transcription.provider import TranscriptionProviderRequest


class FakeTranscriptions:
    def __init__(self, response: Any | None = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else fake_response()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeAudio:
    def __init__(self, transcriptions: FakeTranscriptions) -> None:
        self.transcriptions = transcriptions


class FakeClient:
    def __init__(self, response: Any | None = None, error: Exception | None = None) -> None:
        self.transcriptions = FakeTranscriptions(response, error)
        self.audio = FakeAudio(self.transcriptions)


DEFAULT_USAGE = object()


def fake_response(usage: Any = DEFAULT_USAGE) -> dict[str, Any]:
    payload = {
        "type": "duration",
        "seconds": 3,
    } if usage is DEFAULT_USAGE else usage
    return {
        "text": "Convointel test meeting.",
        "duration": 2.4,
        "segments": [
            {
                "id": "seg_001",
                "start": 0.0,
                "end": 2.4,
                "speaker": "A",
                "text": "Convointel test meeting.",
            }
        ],
        "usage": payload,
    }


def request(audio_path: Path, *, language: str | None = None) -> TranscriptionProviderRequest:
    return TranscriptionProviderRequest(
        meeting_id="mtg_20260720T153045123456Z_a1b2c3d4",
        audio_path=audio_path,
        model="gpt-4o-transcribe-diarize",
        response_format="diarized_json",
        chunking_strategy="auto",
        language=language,
    )


def write_audio(path: Path) -> Path:
    path.write_bytes(b"wav bytes")
    return path


@pytest.mark.parametrize(
    ("language", "expect_language"),
    [(None, False), ("en", True)],
)
def test_openai_provider_request_configuration_and_binary_file(
    tmp_path: Path,
    language: str | None,
    expect_language: bool,
) -> None:
    audio_path = write_audio(tmp_path / "audio.wav")
    client = FakeClient()

    result = OpenAITranscriptionProvider(
        Settings(openai_api_key="test-key"),
        client=client,
    ).transcribe(request(audio_path, language=language))

    call = client.transcriptions.calls[0]
    assert call["model"] == "gpt-4o-transcribe-diarize"
    assert call["response_format"] == "diarized_json"
    assert call["chunking_strategy"] == "auto"
    assert call["stream"] is False
    assert "prompt" not in call
    assert "known_speaker_references" not in call
    assert ("language" in call) is expect_language
    if expect_language:
        assert call["language"] == "en"
    assert "b" in call["file"].mode
    assert call["file"].closed
    assert result.text == "Convointel test meeting."
    assert result.segments[0].speaker_label == "A"


@pytest.mark.parametrize(
    ("usage_payload", "expected_usage"),
    [
        ({"type": "duration", "seconds": 5.5}, DurationTranscriptionUsage(seconds=5.5)),
        (
            {
                "type": "tokens",
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
            },
            TokenTranscriptionUsage(
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
            ),
        ),
        (None, None),
    ],
)
def test_openai_provider_maps_usage_variants(
    tmp_path: Path,
    usage_payload: Any,
    expected_usage: Any,
) -> None:
    audio_path = write_audio(tmp_path / "audio.wav")
    client = FakeClient(response=fake_response(usage_payload))

    result = OpenAITranscriptionProvider(
        Settings(openai_api_key="test-key"),
        client=client,
    ).transcribe(request(audio_path))

    assert result.usage == expected_usage


def test_openai_provider_rejects_malformed_response(tmp_path: Path) -> None:
    audio_path = write_audio(tmp_path / "audio.wav")
    client = FakeClient(response={"text": "missing fields"})

    with pytest.raises(TranscriptionProviderResponseError):
        OpenAITranscriptionProvider(
            Settings(openai_api_key="test-key"),
            client=client,
        ).transcribe(request(audio_path))


def test_openai_provider_derives_duration_when_response_omits_top_level_duration(
    tmp_path: Path,
) -> None:
    audio_path = write_audio(tmp_path / "audio.wav")
    response = fake_response()
    response.pop("duration")
    client = FakeClient(response=response)

    result = OpenAITranscriptionProvider(
        Settings(openai_api_key="test-key"),
        client=client,
    ).transcribe(request(audio_path))

    assert result.duration_seconds == 2.4


def test_openai_provider_missing_key_is_typed_error() -> None:
    with pytest.raises(TranscriptionApiKeyMissingError):
        OpenAITranscriptionProvider(Settings(openai_api_key=None))


def test_openai_client_uses_secret_timeout_and_retry_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class CapturingOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.audio = FakeAudio(FakeTranscriptions())

    monkeypatch.setattr(provider_module, "OpenAI", CapturingOpenAI)

    OpenAITranscriptionProvider(
        Settings(
            openai_api_key="test-key",
            transcription_timeout_seconds=42,
            transcription_max_retries=3,
        )
    )

    assert captured["api_key"] == "test-key"
    assert captured["timeout"] == 42
    assert captured["max_retries"] == 3


def api_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions")


def api_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=api_request())


@pytest.mark.parametrize(
    ("sdk_error", "domain_error"),
    [
        (
            openai.AuthenticationError("auth", response=api_response(401), body=None),
            TranscriptionAuthenticationError,
        ),
        (
            openai.PermissionDeniedError("permission", response=api_response(403), body=None),
            TranscriptionPermissionError,
        ),
        (
            openai.RateLimitError("rate", response=api_response(429), body=None),
            TranscriptionRateLimitError,
        ),
        (
            openai.APIConnectionError(request=api_request()),
            TranscriptionConnectionError,
        ),
        (
            openai.APITimeoutError(request=api_request()),
            TranscriptionTimeoutError,
        ),
        (
            openai.BadRequestError("bad", response=api_response(400), body=None),
            TranscriptionRequestError,
        ),
        (
            openai.InternalServerError("server", response=api_response(500), body=None),
            TranscriptionProviderError,
        ),
    ],
)
def test_openai_provider_maps_sdk_errors(
    tmp_path: Path,
    sdk_error: Exception,
    domain_error: type[Exception],
) -> None:
    audio_path = write_audio(tmp_path / "audio.wav")
    client = FakeClient(error=sdk_error)

    with pytest.raises(domain_error) as exc_info:
        OpenAITranscriptionProvider(
            Settings(openai_api_key="test-key"),
            client=client,
        ).transcribe(request(audio_path))

    assert exc_info.value.__cause__ is sdk_error
