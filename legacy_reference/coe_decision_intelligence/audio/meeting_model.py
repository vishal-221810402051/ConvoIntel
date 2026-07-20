# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntakeResult:
    meeting_id: str
    meeting_dir: Path
    source_dir: Path
    metadata_dir: Path
    logs_dir: Path
    original_audio_path: Path
    intake_metadata_path: Path
    source_file_name: str
    stored_file_name: str
    source_extension: str
    status: str
