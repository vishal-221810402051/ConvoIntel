"""OpenAI diarized transcription provider."""

from __future__ import annotations

from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.models.transcription import (
    DurationTranscriptionUsage,
    TokenTranscriptionUsage,
)
from backend.app.services.transcription.errors import (
    TranscriptionApiKeyMissingError,
    TranscriptionAuthenticationError,
    TranscriptionConnectionError,
    TranscriptionInputNotFoundError,
    TranscriptionPermissionError,
    TranscriptionProviderError,
    TranscriptionProviderResponseError,
    TranscriptionRateLimitError,
    TranscriptionRequestError,
    TranscriptionTimeoutError,
)
from backend.app.services.transcription.provider import (
    TranscriptionProviderRequest,
    TranscriptionProviderResult,
    TranscriptionProviderSegment,
)


class OpenAITranscriptionProvider:
    """Map OpenAI diarized transcription responses into Convointel contracts."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or self._build_client(self._settings)

    def transcribe(
        self,
        request: TranscriptionProviderRequest,
    ) -> TranscriptionProviderResult:
        request_kwargs: dict[str, Any] = {
            "model": request.model,
            "response_format": request.response_format,
            "chunking_strategy": request.chunking_strategy,
            "stream": False,
        }
        if request.language is not None:
            request_kwargs["language"] = request.language

        try:
            with request.audio_path.open("rb") as audio_file:
                response = self._client.audio.transcriptions.create(
                    file=audio_file,
                    **request_kwargs,
                )
        except OSError as exc:
            raise TranscriptionInputNotFoundError(
                "Normalized audio could not be opened for transcription."
            ) from exc
        except AuthenticationError as exc:
            raise TranscriptionAuthenticationError(
                "Transcription provider authentication failed."
            ) from exc
        except PermissionDeniedError as exc:
            raise TranscriptionPermissionError(
                "Transcription provider permission was denied."
            ) from exc
        except RateLimitError as exc:
            raise TranscriptionRateLimitError(
                "Transcription provider rate limit was reached."
            ) from exc
        except APITimeoutError as exc:
            raise TranscriptionTimeoutError(
                "Transcription provider request timed out."
            ) from exc
        except APIConnectionError as exc:
            raise TranscriptionConnectionError(
                "Transcription provider connection failed."
            ) from exc
        except BadRequestError as exc:
            raise TranscriptionRequestError(
                "Transcription provider rejected the request."
            ) from exc
        except InternalServerError as exc:
            raise TranscriptionProviderError(
                "Transcription provider server failure."
            ) from exc
        except APIStatusError as exc:
            if exc.status_code >= 500:
                raise TranscriptionProviderError(
                    "Transcription provider server failure."
                ) from exc
            raise TranscriptionRequestError(
                "Transcription provider rejected the request."
            ) from exc
        except OpenAIError as exc:
            raise TranscriptionProviderError(
                "Transcription provider failed unexpectedly."
            ) from exc

        return self._map_response(response)

    def _build_client(self, settings: Settings) -> OpenAI:
        if settings.openai_api_key is None:
            raise TranscriptionApiKeyMissingError("OpenAI API key is not configured.")

        api_key = settings.openai_api_key.get_secret_value()
        if not api_key.strip():
            raise TranscriptionApiKeyMissingError("OpenAI API key is not configured.")

        return OpenAI(
            api_key=api_key,
            timeout=settings.transcription_timeout_seconds,
            max_retries=settings.transcription_max_retries,
        )

    def _map_response(self, response: Any) -> TranscriptionProviderResult:
        try:
            raw_segments = self._required_list(response, "segments")
            segments = [
                TranscriptionProviderSegment(
                    segment_id=self._optional_text(segment, "id"),
                    start_seconds=self._required_float(segment, "start"),
                    end_seconds=self._required_float(segment, "end"),
                    speaker_label=self._required_text(segment, "speaker"),
                    text=self._required_text(segment, "text"),
                )
                for segment in raw_segments
            ]
            return TranscriptionProviderResult(
                text=self._required_text(response, "text"),
                duration_seconds=self._duration_seconds(response, segments),
                segments=segments,
                usage=self._map_usage(self._optional_value(response, "usage")),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise TranscriptionProviderResponseError(
                "Transcription provider response was invalid."
            ) from exc

    def _map_usage(
        self,
        usage: Any,
    ) -> DurationTranscriptionUsage | TokenTranscriptionUsage | None:
        if usage is None:
            return None

        usage_type = self._required_text(usage, "type")
        if usage_type == "duration":
            return DurationTranscriptionUsage(
                seconds=self._required_float(usage, "seconds"),
            )
        if usage_type == "tokens":
            return TokenTranscriptionUsage(
                input_tokens=self._required_int(usage, "input_tokens"),
                output_tokens=self._required_int(usage, "output_tokens"),
                total_tokens=self._required_int(usage, "total_tokens"),
            )
        raise ValueError("unsupported provider usage type")

    def _optional_value(self, source: Any, name: str) -> Any:
        if isinstance(source, dict):
            return source.get(name)
        return getattr(source, name, None)

    def _optional_text(self, source: Any, name: str) -> str | None:
        value = self._optional_value(source, name)
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        normalized = value.strip()
        return normalized or None

    def _required_text(self, source: Any, name: str) -> str:
        value = self._optional_value(source, name)
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        return value

    def _required_list(self, source: Any, name: str) -> list[Any]:
        value = self._optional_value(source, name)
        if not isinstance(value, list):
            raise TypeError(f"{name} must be a list")
        return value

    def _required_float(self, source: Any, name: str) -> float:
        value = self._optional_value(source, name)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{name} must be numeric") from exc

    def _required_int(self, source: Any, name: str) -> int:
        value = self._optional_value(source, name)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{name} must be numeric") from exc

    def _duration_seconds(
        self,
        response: Any,
        segments: list[TranscriptionProviderSegment],
    ) -> float:
        value = self._optional_value(response, "duration")
        if value is not None:
            return self._required_float(response, "duration")
        if not segments:
            return 0.0
        return max(segment.end_seconds for segment in segments)
