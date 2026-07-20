"""Domain errors for canonical audio normalization."""


class AudioNormalizationError(RuntimeError):
    """Base class for local audio-normalization failures."""


class MeetingPackageNotFoundError(AudioNormalizationError):
    """Raised when the requested meeting package does not exist."""


class MeetingManifestNotFoundError(AudioNormalizationError):
    """Raised when the Phase 2 meeting manifest is missing."""


class MeetingManifestInvalidError(AudioNormalizationError):
    """Raised when the Phase 2 meeting manifest is invalid for normalization."""


class SourceAudioIntegrityError(AudioNormalizationError):
    """Raised when source audio no longer matches the Phase 2 manifest."""


class SourceAudioMissingError(AudioNormalizationError):
    """Raised when the source audio path from the manifest is missing."""


class FfmpegNotAvailableError(AudioNormalizationError):
    """Raised when the configured FFmpeg executable cannot be started."""


class FfprobeNotAvailableError(AudioNormalizationError):
    """Raised when the configured FFprobe executable cannot be started."""


class NormalizationTimeoutError(AudioNormalizationError):
    """Raised when a normalization subprocess exceeds the configured timeout."""


class NormalizationProcessError(AudioNormalizationError):
    """Raised when FFmpeg fails to convert the source audio."""


class NormalizedAudioValidationError(AudioNormalizationError):
    """Raised when normalized audio fails FFprobe or checksum validation."""


class NormalizationStateError(AudioNormalizationError):
    """Raised when final normalization artifacts are missing or inconsistent."""


class NormalizationMetadataWriteError(AudioNormalizationError):
    """Raised when normalization metadata cannot be written."""


class NormalizationPublicationError(AudioNormalizationError):
    """Raised when staged normalization artifacts cannot be published."""
