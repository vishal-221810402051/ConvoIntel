# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
from pathlib import Path

from app.services.audio import AudioIntakeService


def test_audio_intake_smoke(tmp_path: Path) -> None:
    source_file = tmp_path / "sample.wav"
    source_file.write_bytes(b"fake wav content")

    processed_root = tmp_path / "processed"
    service = AudioIntakeService(processed_root=processed_root)

    result = service.intake_audio(source_file)

    assert result.meeting_dir.exists()
    assert result.source_dir.exists()
    assert result.metadata_dir.exists()
    assert result.logs_dir.exists()
    assert result.original_audio_path.exists()
    assert result.original_audio_path.name == "original.wav"
    assert result.intake_metadata_path.exists()

    metadata = json.loads(result.intake_metadata_path.read_text(encoding="utf-8"))
    assert metadata["meeting_id"] == result.meeting_id
    assert metadata["source_file_name"] == "sample.wav"
    assert metadata["stored_file_name"] == "original.wav"
    assert metadata["source_extension"] == ".wav"
    assert metadata["status"] == "intake_completed"
