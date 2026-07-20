# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.audio import AudioNormalizationService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke test for Phase 02 audio normalization."
    )
    parser.add_argument("meeting_id", help="Meeting ID under data/processed/")
    args = parser.parse_args()

    service = AudioNormalizationService()
    result = service.normalize_meeting(args.meeting_id)

    print("Audio normalization completed successfully.")
    print(f"meeting_id={result.meeting_id}")
    print(f"input_audio_path={result.input_audio_path}")
    print(f"output_audio_path={result.output_audio_path}")
    print(f"normalization_metadata_path={result.normalization_metadata_path}")
    print(f"overwrite_policy={result.overwrite_policy}")
    print(f"status={result.status}")


if __name__ == "__main__":
    main()
