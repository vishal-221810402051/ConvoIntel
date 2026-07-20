"""Domain errors for local source-audio intake."""


class AudioIntakeError(RuntimeError):
    """Base class for local audio-intake failures."""


class SourceAudioNotFoundError(AudioIntakeError):
    """Raised when the source audio path does not exist."""


class SourceAudioNotFileError(AudioIntakeError):
    """Raised when the source path is not a regular file."""


class UnsupportedAudioFormatError(AudioIntakeError):
    """Raised when the source audio extension is not supported."""


class EmptyAudioFileError(AudioIntakeError):
    """Raised when the source audio file has zero bytes."""


class SourceAudioReadError(AudioIntakeError):
    """Raised when the source audio cannot be opened or read."""


class MeetingIdCollisionError(AudioIntakeError):
    """Raised when a unique meeting identifier cannot be generated."""


class MeetingPackageWriteError(AudioIntakeError):
    """Raised when the canonical meeting package cannot be written."""
