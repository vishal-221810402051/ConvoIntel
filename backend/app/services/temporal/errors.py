"""Typed errors for temporal intelligence extraction."""


class TemporalIntelligenceError(Exception):
    """Base class for Phase 7 temporal intelligence failures."""


class TemporalConfigurationError(TemporalIntelligenceError):
    """The runtime temporal reference or configuration is invalid."""


class TemporalInputNotFoundError(TemporalIntelligenceError):
    """A required upstream artifact is missing."""


class TemporalInputIntegrityError(TemporalIntelligenceError):
    """A required upstream artifact failed integrity validation."""


class TemporalInputTooLargeError(TemporalIntelligenceError):
    """The deterministic provider input exceeds the configured size limit."""


class TemporalStateError(TemporalIntelligenceError):
    """Existing temporal artifacts are partial or incompatible."""


class TemporalProviderError(TemporalIntelligenceError):
    """The temporal provider failed unexpectedly."""


class TemporalApiKeyMissingError(TemporalProviderError):
    """The OpenAI API key is not configured."""


class TemporalAuthenticationError(TemporalProviderError):
    """The temporal provider rejected authentication."""


class TemporalPermissionError(TemporalProviderError):
    """The temporal provider denied permission."""


class TemporalRateLimitError(TemporalProviderError):
    """The temporal provider rate limit was reached."""


class TemporalConnectionError(TemporalProviderError):
    """The temporal provider could not be reached."""


class TemporalTimeoutError(TemporalProviderError):
    """The temporal provider request timed out."""


class TemporalRequestError(TemporalProviderError):
    """The temporal provider rejected the request contract."""


class TemporalProviderResponseError(TemporalIntelligenceError):
    """The temporal provider response was malformed or unsafe."""


class TemporalEvidenceError(TemporalProviderResponseError):
    """A temporal item has invalid transcript evidence."""


class TemporalIntelligenceReferenceError(TemporalProviderResponseError):
    """A temporal item has invalid Phase 6 intelligence links."""


class TemporalNormalizationError(TemporalProviderResponseError):
    """A temporal value could not be normalized safely."""


class TemporalTimezoneError(TemporalConfigurationError, TemporalNormalizationError):
    """An IANA timezone is unsupported or unavailable."""


class TemporalPublicationError(TemporalIntelligenceError):
    """Temporal artifacts could not be written or published."""


class TemporalMetadataWriteError(TemporalPublicationError):
    """Temporal metadata could not be written."""
