"""Domain errors for general decision intelligence."""


class DecisionIntelligenceError(RuntimeError):
    """Base class for decision-intelligence failures."""


class IntelligenceConfigurationError(DecisionIntelligenceError):
    """Raised when intelligence configuration is invalid."""


class IntelligenceApiKeyMissingError(IntelligenceConfigurationError):
    """Raised when no usable OpenAI API key is configured."""


class IntelligenceInputNotFoundError(DecisionIntelligenceError):
    """Raised when required Phase 1-5 input artifacts are missing."""


class IntelligenceInputIntegrityError(DecisionIntelligenceError):
    """Raised when Phase 1-5 input artifacts fail integrity checks."""


class IntelligenceInputTooLargeError(DecisionIntelligenceError):
    """Raised when deterministic semantic input exceeds the configured limit."""


class IntelligenceAuthenticationError(DecisionIntelligenceError):
    """Raised when the provider rejects authentication."""


class IntelligencePermissionError(DecisionIntelligenceError):
    """Raised when the provider denies permission."""


class IntelligenceRateLimitError(DecisionIntelligenceError):
    """Raised when the provider rate-limits the request."""


class IntelligenceConnectionError(DecisionIntelligenceError):
    """Raised when the provider cannot be reached."""


class IntelligenceTimeoutError(DecisionIntelligenceError):
    """Raised when the provider request times out."""


class IntelligenceRequestError(DecisionIntelligenceError):
    """Raised when the provider rejects the intelligence request."""


class IntelligenceProviderError(DecisionIntelligenceError):
    """Raised when the provider fails unexpectedly."""


class IntelligenceProviderResponseError(DecisionIntelligenceError):
    """Raised when the provider response cannot be mapped safely."""


class IntelligenceEvidenceError(DecisionIntelligenceError):
    """Raised when provider evidence cannot be grounded locally."""


class IntelligenceActorError(DecisionIntelligenceError):
    """Raised when provider actor references are unsupported."""


class IntelligenceDeadlineError(DecisionIntelligenceError):
    """Raised when provider deadline references are unsupported."""


class IntelligenceStateError(DecisionIntelligenceError):
    """Raised when completed intelligence artifacts are inconsistent."""


class IntelligenceMetadataWriteError(DecisionIntelligenceError):
    """Raised when intelligence metadata cannot be written."""


class IntelligencePublicationError(DecisionIntelligenceError):
    """Raised when staged intelligence artifacts cannot be published."""
