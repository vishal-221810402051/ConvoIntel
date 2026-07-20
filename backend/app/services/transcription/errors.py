"""Domain errors for diarized raw transcription."""


class TranscriptionError(RuntimeError):
    """Base class for transcription failures."""


class TranscriptionConfigurationError(TranscriptionError):
    """Raised when transcription configuration is invalid."""


class TranscriptionApiKeyMissingError(TranscriptionConfigurationError):
    """Raised when no usable OpenAI API key is configured."""


class TranscriptionAuthenticationError(TranscriptionError):
    """Raised when the provider rejects authentication."""


class TranscriptionPermissionError(TranscriptionError):
    """Raised when the provider denies permission."""


class TranscriptionRateLimitError(TranscriptionError):
    """Raised when the provider rate-limits the request."""


class TranscriptionConnectionError(TranscriptionError):
    """Raised when the provider cannot be reached."""


class TranscriptionTimeoutError(TranscriptionError):
    """Raised when the provider request times out."""


class TranscriptionRequestError(TranscriptionError):
    """Raised when the provider rejects the transcription request."""


class TranscriptionProviderError(TranscriptionError):
    """Raised when the provider fails unexpectedly."""


class TranscriptionProviderResponseError(TranscriptionError):
    """Raised when the provider response cannot be mapped safely."""


class TranscriptionInputNotFoundError(TranscriptionError):
    """Raised when required normalized input artifacts are missing."""


class TranscriptionInputIntegrityError(TranscriptionError):
    """Raised when normalized input artifacts fail integrity checks."""


class TranscriptionStateError(TranscriptionError):
    """Raised when completed transcription artifacts are inconsistent."""


class TranscriptionMetadataWriteError(TranscriptionError):
    """Raised when transcription metadata cannot be written."""


class TranscriptionPublicationError(TranscriptionError):
    """Raised when staged transcription artifacts cannot be published."""
