# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NormalizationResult:
    meeting_id: str
    meeting_dir: Path
    input_audio_path: Path
    normalized_dir: Path
    output_audio_path: Path
    normalization_metadata_path: Path
    output_file_name: str
    channels: int
    sample_rate_hz: int
    codec: str
    overwrite_policy: str
    status: str
