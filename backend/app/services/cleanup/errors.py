"""Domain errors for transcript cleanup."""


class TranscriptCleanupError(RuntimeError):
    """Base class for transcript cleanup failures."""


class CleanupConfigurationError(TranscriptCleanupError):
    """Raised when cleanup configuration is invalid."""


class CleanupApiKeyMissingError(CleanupConfigurationError):
    """Raised when no usable OpenAI API key is configured."""


class CleanupInputNotFoundError(TranscriptCleanupError):
    """Raised when required Phase 1-4 input artifacts are missing."""


class CleanupInputIntegrityError(TranscriptCleanupError):
    """Raised when Phase 1-4 input artifacts fail integrity checks."""


class CleanupAuthenticationError(TranscriptCleanupError):
    """Raised when the provider rejects authentication."""


class CleanupPermissionError(TranscriptCleanupError):
    """Raised when the provider denies permission."""


class CleanupRateLimitError(TranscriptCleanupError):
    """Raised when the provider rate-limits the request."""


class CleanupConnectionError(TranscriptCleanupError):
    """Raised when the provider cannot be reached."""


class CleanupTimeoutError(TranscriptCleanupError):
    """Raised when the provider request times out."""


class CleanupRequestError(TranscriptCleanupError):
    """Raised when the provider rejects the cleanup request."""


class CleanupProviderError(TranscriptCleanupError):
    """Raised when the provider fails unexpectedly."""


class CleanupProviderResponseError(TranscriptCleanupError):
    """Raised when the provider response cannot be mapped safely."""


class CleanupFidelityError(TranscriptCleanupError):
    """Raised when a cleaned transcript violates local fidelity guards."""


class CleanupStateError(TranscriptCleanupError):
    """Raised when completed cleanup artifacts are inconsistent."""


class CleanupMetadataWriteError(TranscriptCleanupError):
    """Raised when cleanup metadata cannot be written."""


class CleanupPublicationError(TranscriptCleanupError):
    """Raised when staged cleanup artifacts cannot be published."""
