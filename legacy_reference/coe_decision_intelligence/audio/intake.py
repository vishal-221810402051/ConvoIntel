# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import config
from app.models import IntakeResult


class AudioIntakeService:
    def __init__(self, processed_root: Path | None = None) -> None:
        self.processed_root = processed_root or config.PROCESSED_PATH
        self.allowed_extensions = config.ALLOWED_AUDIO_EXTENSIONS

    def intake_audio(self, source_path: str | Path) -> IntakeResult:
        source = Path(source_path).expanduser().resolve()

        if not source.exists():
            raise FileNotFoundError(f"Source audio file not found: {source}")

        if not source.is_file():
            raise ValueError(f"Source path is not a file: {source}")

        extension = source.suffix.lower()
        if extension not in self.allowed_extensions:
            allowed = ", ".join(sorted(self.allowed_extensions))
            raise ValueError(
                f"Unsupported audio format '{extension}'. Allowed formats: {allowed}"
            )

        meeting_id = self._generate_meeting_id()
        meeting_dir = self.processed_root / meeting_id
        source_dir = meeting_dir / "source"
        metadata_dir = meeting_dir / "metadata"
        logs_dir = meeting_dir / "logs"

        source_dir.mkdir(parents=True, exist_ok=False)
        metadata_dir.mkdir(parents=True, exist_ok=False)
        logs_dir.mkdir(parents=True, exist_ok=False)

        stored_file_name = f"original{extension}"
        stored_audio_path = source_dir / stored_file_name

        shutil.copy2(source, stored_audio_path)

        intake_metadata_path = metadata_dir / "intake.json"
        created_at = datetime.now().isoformat(timespec="seconds")

        metadata = {
            "meeting_id": meeting_id,
            "created_at": created_at,
            "source_file_name": source.name,
            "stored_file_name": stored_file_name,
            "source_extension": extension,
            "source_path": str(source),
            "stored_path": str(stored_audio_path),
            "status": "intake_completed",
        }

        with intake_metadata_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        return IntakeResult(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            source_dir=source_dir,
            metadata_dir=metadata_dir,
            logs_dir=logs_dir,
            original_audio_path=stored_audio_path,
            intake_metadata_path=intake_metadata_path,
            source_file_name=source.name,
            stored_file_name=stored_file_name,
            source_extension=extension,
            status="intake_completed",
        )

    @staticmethod
    def _generate_meeting_id() -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        short_uuid = uuid4().hex[:6]
        return f"MTG-{timestamp}-{short_uuid}"
