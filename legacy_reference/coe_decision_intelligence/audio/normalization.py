# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from app.config import config
from app.models.normalization import NormalizationResult


class AudioNormalizationService:
    def __init__(self, processed_root: Path | None = None) -> None:
        self.processed_root = processed_root or config.PROCESSED_PATH
        self.allowed_extensions = config.ALLOWED_AUDIO_EXTENSIONS
        self.ffmpeg_binary = config.FFMPEG_BINARY

    def normalize_meeting(self, meeting_id: str) -> NormalizationResult:
        meeting_dir = self.processed_root / meeting_id
        if not meeting_dir.exists():
            raise FileNotFoundError(f"Meeting directory not found: {meeting_dir}")

        source_dir = meeting_dir / "source"
        if not source_dir.exists():
            raise FileNotFoundError(f"Source directory not found: {source_dir}")

        source_candidates = [p for p in source_dir.glob("original.*") if p.is_file()]
        if not source_candidates:
            raise FileNotFoundError(f"Source audio file not found in: {source_dir}")
        if len(source_candidates) > 1:
            raise ValueError(
                f"Multiple source audio files found in {source_dir}: "
                f"{', '.join(sorted(p.name for p in source_candidates))}"
            )

        input_audio_path = source_candidates[0]
        extension = input_audio_path.suffix.lower()
        if extension not in self.allowed_extensions:
            allowed = ", ".join(sorted(self.allowed_extensions))
            raise ValueError(
                f"Unsupported audio format '{extension}'. Allowed formats: {allowed}"
            )

        metadata_dir = meeting_dir / "metadata"
        if not metadata_dir.exists():
            raise FileNotFoundError(f"Metadata directory not found: {metadata_dir}")

        normalized_dir = meeting_dir / config.NORMALIZED_DIR_NAME
        normalized_dir.mkdir(parents=True, exist_ok=True)

        output_audio_path = normalized_dir / config.NORMALIZED_AUDIO_FILE_NAME
        normalization_metadata_path = (
            metadata_dir / config.NORMALIZATION_METADATA_FILE_NAME
        )

        command = [
            self.ffmpeg_binary,
            "-y",
            "-i",
            str(input_audio_path),
            "-ac",
            str(config.NORMALIZATION_CHANNELS),
            "-ar",
            str(config.NORMALIZATION_SAMPLE_RATE_HZ),
            "-c:a",
            config.NORMALIZATION_CODEC,
            str(output_audio_path),
        ]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"FFmpeg executable not found: {self.ffmpeg_binary}"
            ) from exc

        if completed.returncode != 0:
            if output_audio_path.exists():
                output_audio_path.unlink()

            stderr_summary = self._stderr_summary(completed.stderr)
            raise RuntimeError(
                f"FFmpeg conversion failed for meeting '{meeting_id}'. "
                f"stderr: {stderr_summary}"
            )

        created_at = datetime.now().isoformat(timespec="seconds")
        metadata = {
            "meeting_id": meeting_id,
            "created_at": created_at,
            "input_path": str(input_audio_path),
            "output_path": str(output_audio_path),
            "output_file_name": config.NORMALIZED_AUDIO_FILE_NAME,
            "channels": config.NORMALIZATION_CHANNELS,
            "sample_rate_hz": config.NORMALIZATION_SAMPLE_RATE_HZ,
            "codec": config.NORMALIZATION_CODEC,
            "overwrite_policy": config.NORMALIZATION_OVERWRITE_POLICY,
            "status": "normalization_completed",
        }

        with normalization_metadata_path.open("w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=2, ensure_ascii=False)

        return NormalizationResult(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            input_audio_path=input_audio_path,
            normalized_dir=normalized_dir,
            output_audio_path=output_audio_path,
            normalization_metadata_path=normalization_metadata_path,
            output_file_name=config.NORMALIZED_AUDIO_FILE_NAME,
            channels=config.NORMALIZATION_CHANNELS,
            sample_rate_hz=config.NORMALIZATION_SAMPLE_RATE_HZ,
            codec=config.NORMALIZATION_CODEC,
            overwrite_policy=config.NORMALIZATION_OVERWRITE_POLICY,
            status="normalization_completed",
        )

    @staticmethod
    def _stderr_summary(stderr: str) -> str:
        cleaned = "\n".join(line for line in stderr.splitlines() if line.strip())
        if not cleaned:
            return "no stderr output"
        lines = cleaned.splitlines()
        return " | ".join(lines[-5:])
