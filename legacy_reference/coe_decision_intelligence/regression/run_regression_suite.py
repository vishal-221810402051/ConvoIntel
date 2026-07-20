# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.regression import run_regression_suite


def main() -> None:
    report = run_regression_suite()
    report_path = Path("benchmarks") / "reports" / "latest_regression_report.json"
    print("Regression suite completed.")
    print(f"total_meetings={report.total_meetings}")
    print(f"passed_meetings={report.passed_meetings}")
    print(f"failed_meetings={report.failed_meetings}")
    print(f"report={report_path}")


if __name__ == "__main__":
    main()

