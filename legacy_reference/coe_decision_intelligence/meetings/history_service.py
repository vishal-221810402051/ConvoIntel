# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import config
from app.ui.repository import (
    find_report_pdf,
    get_artifact_paths,
    list_meetings,
    safe_read_json,
)
from app.ui.status_model import compute_stage_status


MEETING_ID_PATTERN = re.compile(r"^MTG-[A-Za-z0-9-]+$")


def is_valid_meeting_id(meeting_id: str) -> bool:
    token = str(meeting_id or "").strip()
    if not token:
        return False
    if any(ch in token for ch in ("/", "\\", "..")):
        return False
    return bool(MEETING_ID_PATTERN.fullmatch(token))


def _safe_read_json_file(path: Path) -> dict[str, Any]:
    payload = safe_read_json(path)
    if isinstance(payload, dict):
        return payload
    return {}


def _meeting_dir(meeting_id: str) -> Path:
    return config.PROCESSED_PATH / str(meeting_id).strip()


def _report_payload(meeting_id: str) -> dict[str, Any]:
    path = _meeting_dir(meeting_id) / config.REPORT_DIR_NAME / config.REPORT_PAYLOAD_FILE
    return _safe_read_json_file(path)


def _report_metadata(meeting_id: str) -> dict[str, Any]:
    path = _meeting_dir(meeting_id) / config.REPORT_DIR_NAME / config.REPORT_METADATA_FILE
    return _safe_read_json_file(path)


def _calendar_metadata(meeting_id: str) -> dict[str, Any]:
    path = _meeting_dir(meeting_id) / "calendar" / "calendar_candidate_metadata.json"
    return _safe_read_json_file(path)


def _calendar_candidates(meeting_id: str) -> dict[str, Any]:
    path = _meeting_dir(meeting_id) / "calendar" / "calendar_candidates.json"
    return _safe_read_json_file(path)


def _first_non_empty(values: list[str]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _derive_label(meeting_id: str, report_payload: dict[str, Any]) -> str:
    sections = report_payload.get("sections", {})
    if isinstance(sections, dict):
        summary = sections.get("executive_summary", [])
        if isinstance(summary, list):
            for item in summary:
                text = str(item or "").strip()
                if text:
                    return text
    return f"Meeting {meeting_id}"


def _approval_counts(meeting_id: str) -> dict[str, int]:
    metadata = _calendar_metadata(meeting_id)
    if metadata:
        return {
            "candidate_count": int(metadata.get("candidate_count", 0) or 0),
            "pending_count": int(metadata.get("pending_count", 0) or 0),
            "approved_count": int(metadata.get("approved_count", 0) or 0),
            "rejected_count": int(metadata.get("rejected_count", 0) or 0),
            "blocked_count": int(metadata.get("blocked_count", 0) or 0),
        }

    payload = _calendar_candidates(meeting_id)
    rows = payload.get("candidates", [])
    if not isinstance(rows, list):
        rows = []
    return {
        "candidate_count": len(rows),
        "pending_count": sum(1 for row in rows if str(row.get("approval_state", "")).strip() == "pending"),
        "approved_count": sum(1 for row in rows if str(row.get("approval_state", "")).strip() == "approved"),
        "rejected_count": sum(1 for row in rows if str(row.get("approval_state", "")).strip() == "rejected"),
        "blocked_count": sum(1 for row in rows if str(row.get("eligibility_status", "")).strip() == "blocked"),
    }


def _processing_status(meeting_id: str, report_metadata: dict[str, Any], stage_statuses: dict[str, str]) -> str:
    report_status = str(report_metadata.get("status", "")).strip()
    if report_status == "completed":
        return "completed"
    if report_status == "blocked":
        return "failed"

    stage_values = [str(value).strip() for value in stage_statuses.values()]
    if any(value == "unknown" for value in stage_values):
        return "partial"
    if any(value == "completed" for value in stage_values):
        return "processing"
    return "unknown"


def resolve_report_pdf(meeting_id: str) -> Path | None:
    return find_report_pdf(str(meeting_id).strip())


def _artifact_flags(meeting_id: str) -> dict[str, bool]:
    paths = get_artifact_paths(meeting_id)
    meeting_dir = _meeting_dir(meeting_id)
    return {
        "source_audio": paths["source_audio"].exists(),
        "normalized_audio": paths["normalized_audio"].exists(),
        "transcript_raw": paths["raw_transcript"].exists(),
        "transcript_clean": paths["clean_transcript"].exists(),
        "intelligence": paths["intelligence"].exists(),
        "executive": paths["executive"].exists(),
        "decision": paths["decision"].exists(),
        "temporal": (meeting_dir / "temporal" / "temporal_intelligence.json").exists(),
        "calendar": (meeting_dir / "calendar" / "calendar_candidates.json").exists(),
        "report_payload": paths["report_payload"].exists(),
        "report_html": paths["report_html"].exists(),
        "report_pdf": paths["report_pdf"].exists(),
    }


def get_meeting_detail(meeting_id: str) -> dict[str, Any]:
    meeting_key = str(meeting_id).strip()
    meeting_dir = _meeting_dir(meeting_key)
    if not meeting_dir.exists() or not meeting_dir.is_dir():
        raise FileNotFoundError(f"Meeting not found: {meeting_key}")

    intake = _safe_read_json_file(meeting_dir / "metadata" / "intake.json")
    created_at = str(intake.get("created_at", "")).strip()
    report_meta = _report_metadata(meeting_key)
    payload = _report_payload(meeting_key)
    stage_statuses = compute_stage_status(meeting_key)
    counts = _approval_counts(meeting_key)
    label = _derive_label(meeting_key, payload)
    pdf_path = resolve_report_pdf(meeting_key)
    processed_at = _first_non_empty(
        [
            str(report_meta.get("generated_at", "")).strip(),
            str(report_meta.get("updated_at", "")).strip(),
        ]
    )

    return {
        "meeting_id": meeting_key,
        "created_at": created_at,
        "processed_at": processed_at,
        "label": label,
        "processing_status": _processing_status(meeting_key, report_meta, stage_statuses),
        "stage_statuses": stage_statuses,
        "report": {
            "available": bool(report_meta),
            "pdf_available": pdf_path is not None,
            "pdf_status": str(report_meta.get("pdf_status", "")).strip(),
            "generated_at": str(report_meta.get("generated_at", "")).strip(),
        },
        "calendar": counts,
        "pending_approvals_count": counts["pending_count"],
        "approved_count": counts["approved_count"],
        "artifacts": _artifact_flags(meeting_key),
    }


def list_recent_meetings(limit: int = 5) -> list[dict[str, Any]]:
    capped_limit = max(1, min(int(limit or 5), 50))
    rows: list[dict[str, Any]] = []
    for row in list_meetings():
        meeting_id = str(row.get("meeting_id", "")).strip()
        if not is_valid_meeting_id(meeting_id):
            continue
        try:
            detail = get_meeting_detail(meeting_id)
        except Exception:
            continue
        rows.append(
            {
                "meeting_id": detail["meeting_id"],
                "created_at": detail["created_at"],
                "processed_at": detail["processed_at"],
                "label": detail["label"],
                "processing_status": detail["processing_status"],
                "pdf_available": bool(detail["report"]["pdf_available"]),
                "pending_approvals_count": int(detail.get("pending_approvals_count", 0) or 0),
                "approved_count": int(detail.get("approved_count", 0) or 0),
                "has_report": bool(detail["report"]["available"]),
            }
        )
        if len(rows) >= capped_limit:
            break
    return rows


def _assert_inside(path: Path, root: Path) -> None:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    resolved_path.relative_to(resolved_root)


def _archive_base_for(meeting_id: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return config.ARCHIVE_PATH / f"{meeting_id}__{stamp}"


def archive_meeting(meeting_id: str) -> dict[str, Any]:
    meeting_key = str(meeting_id).strip()
    if not is_valid_meeting_id(meeting_key):
        raise ValueError("invalid_meeting_id")

    processed_path = config.PROCESSED_PATH / meeting_key
    raw_path = (config.DATA_PATH / "raw" / meeting_key).resolve()
    if not processed_path.exists() or not processed_path.is_dir():
        raise FileNotFoundError(f"Meeting not found: {meeting_key}")

    _assert_inside(processed_path, config.PROCESSED_PATH)
    if raw_path.exists():
        _assert_inside(raw_path, config.DATA_PATH / "raw")

    base = _archive_base_for(meeting_key)
    base.mkdir(parents=True, exist_ok=False)

    archived_processed = base / "processed"
    shutil.move(str(processed_path), str(archived_processed))

    archived_raw = None
    if raw_path.exists() and raw_path.is_dir():
        archived_raw = base / "raw"
        shutil.move(str(raw_path), str(archived_raw))

    manifest = {
        "meeting_id": meeting_key,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "processed_path": str(archived_processed),
        "raw_path": str(archived_raw) if archived_raw is not None else "",
    }
    (base / "archive_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "meeting_id": meeting_key,
        "status": "deleted",
        "mode": "archived",
        "archive_path": str(base),
    }
