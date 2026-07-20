"""Domain errors for deterministic calendar recommendations."""


class CalendarRecommendationError(RuntimeError):
    """Base class for Phase 8 calendar recommendation failures."""


class CalendarInputNotFoundError(CalendarRecommendationError):
    """A required upstream artifact is missing."""


class CalendarInputIntegrityError(CalendarRecommendationError):
    """A required upstream artifact failed integrity validation."""


class CalendarPolicyError(CalendarRecommendationError):
    """Deterministic recommendation policy could not build a valid result."""


class CalendarScheduleError(CalendarPolicyError):
    """A schedule shape is invalid or cannot be represented safely."""


class CalendarConflictError(CalendarPolicyError):
    """Conflicting temporal components block recommendation construction."""


class CalendarStateError(CalendarRecommendationError):
    """Existing Phase 8 artifacts are partial or incompatible."""


class CalendarMetadataWriteError(CalendarRecommendationError):
    """Calendar recommendation metadata could not be written."""


class CalendarPublicationError(CalendarRecommendationError):
    """Calendar recommendation artifacts could not be published."""
