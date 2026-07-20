# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.regression import run_repeat_run_check


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/repeat_run_check.py <meeting_id>")
        sys.exit(1)

    meeting_id = sys.argv[1]
    report = run_repeat_run_check(meeting_id)
    report_path = (
        Path("data")
        / "processed"
        / meeting_id
        / "regression"
        / "repeat_run_report.json"
    )
    print("Repeat-run regression completed.")
    print(f"meeting_id={meeting_id}")
    print(f"runs={report.runs}")
    print(f"pass_status={report.pass_status}")
    print(f"report={report_path}")


if __name__ == "__main__":
    main()

