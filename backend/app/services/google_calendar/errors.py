"""Domain errors for explicitly approved Google Calendar sync."""


class GoogleCalendarSyncError(RuntimeError):
    """Base class for Phase 9 Google Calendar sync failures."""


class GoogleCalendarConfigurationError(GoogleCalendarSyncError):
    """Google Calendar sync configuration is invalid."""


class GoogleCalendarClientSecretMissingError(GoogleCalendarConfigurationError):
    """The OAuth client-secret JSON file is missing."""


class GoogleCalendarClientSecretInvalidError(GoogleCalendarConfigurationError):
    """The OAuth client-secret JSON file is malformed or unsupported."""


class GoogleCalendarAuthorizationRequiredError(GoogleCalendarSyncError):
    """Authorized Google Calendar credentials are required before sync."""


class GoogleCalendarTokenInvalidError(GoogleCalendarAuthorizationRequiredError):
    """The authorized-user token file is malformed or unusable."""


class GoogleCalendarTokenRefreshError(GoogleCalendarAuthorizationRequiredError):
    """The authorized-user token could not be refreshed."""


class GoogleCalendarApprovalRequiredError(GoogleCalendarSyncError):
    """The recommendation was not explicitly approved at runtime."""


class GoogleCalendarRecommendationNotFoundError(GoogleCalendarSyncError):
    """The requested Phase 8 recommendation does not exist."""


class GoogleCalendarRecommendationNotReadyError(GoogleCalendarSyncError):
    """The requested Phase 8 recommendation is not ready for sync."""


class GoogleCalendarRecommendationUnsupportedError(GoogleCalendarSyncError):
    """The requested recommendation cannot be represented safely as an event."""


class GoogleCalendarPayloadError(GoogleCalendarSyncError):
    """The Google Calendar event payload cannot be built safely."""


class GoogleCalendarAuthenticationError(GoogleCalendarSyncError):
    """Google rejected the provided credentials."""


class GoogleCalendarPermissionError(GoogleCalendarSyncError):
    """Google denied access to the target calendar operation."""


class GoogleCalendarCalendarNotFoundError(GoogleCalendarSyncError):
    """The target calendar was not found."""


class GoogleCalendarRateLimitError(GoogleCalendarSyncError):
    """Google Calendar rate limiting prevented the operation."""


class GoogleCalendarConnectionError(GoogleCalendarSyncError):
    """A Google Calendar network connection failed."""


class GoogleCalendarTimeoutError(GoogleCalendarSyncError):
    """A Google Calendar request timed out."""


class GoogleCalendarProviderError(GoogleCalendarSyncError):
    """Google Calendar returned an unexpected provider error."""


class GoogleCalendarRemoteConflictError(GoogleCalendarSyncError):
    """A remote event exists but does not match Convointel provenance."""


class GoogleCalendarRemoteStateError(GoogleCalendarSyncError):
    """The remote Convointel event exists in an unusable state."""


class GoogleCalendarSyncStateError(GoogleCalendarSyncError):
    """Existing local Phase 9 sync state is partial or incompatible."""


class GoogleCalendarMetadataWriteError(GoogleCalendarSyncError):
    """Google Calendar sync metadata could not be written."""


class GoogleCalendarPublicationError(GoogleCalendarSyncError):
    """Google Calendar sync artifacts could not be published."""


class GoogleCalendarLocalPersistenceAfterRemoteSuccessError(GoogleCalendarSyncError):
    """Local Phase 9 persistence failed after a remote event was confirmed."""

    def __init__(self, message: str, *, event_id: str) -> None:
        super().__init__(message)
        self.event_id = event_id
