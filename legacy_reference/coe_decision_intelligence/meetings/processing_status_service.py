# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import config
from app.ui.repository import safe_read_json
from app.ui.status_model import compute_stage_status


STAGE_ORDER: tuple[str, ...] = (
    "pipeline_triggered",
    "normalization",
    "transcription",
    "cleanup",
    "intelligence",
    "executive",
    "decision",
    "temporal",
    "calendar",
    "report",
)

ANDROID_STAGE_STATUSES = {"pending", "running", "completed", "failed", "missing"}


def _safe_read_json_file(path: Path) -> dict[str, Any]:
    payload = safe_read_json(path)
    if isinstance(payload, dict):
        return payload
    return {}


def _meeting_dir(meeting_id: str) -> Path:
    return config.PROCESSED_PATH / str(meeting_id).strip()


def _metadata_dir(meeting_id: str) -> Path:
    return _meeting_dir(meeting_id) / "metadata"


def _stage_file_paths(meeting_id: str) -> list[Path]:
    meeting_dir = _meeting_dir(meeting_id)
    metadata_dir = _metadata_dir(meeting_id)
    return [
        metadata_dir / "intake.json",
        metadata_dir / "normalization.json",
        metadata_dir / "transcription.json",
        metadata_dir / "cleanup.json",
        metadata_dir / "intelligence_metadata.json",
        metadata_dir / "executive_metadata.json",
        metadata_dir / "decision_v2_metadata.json",
        meeting_dir / "temporal" / "temporal_metadata.json",
        meeting_dir / "calendar" / "calendar_candidate_metadata.json",
        meeting_dir / "calendar" / "calendar_candidates.json",
        meeting_dir / config.REPORT_DIR_NAME / config.REPORT_METADATA_FILE,
        meeting_dir / config.REPORT_DIR_NAME / config.REPORT_PAYLOAD_FILE,
        meeting_dir / config.REPORT_DIR_NAME / config.REPORT_HTML_FILE,
        meeting_dir / config.REPORT_DIR_NAME / config.REPORT_PDF_FILE,
    ]


def _latest_updated_at(meeting_id: str) -> str:
    newest: float | None = None
    for path in _stage_file_paths(meeting_id):
        try:
            if path.exists():
                stat = path.stat()
                newest = max(newest, stat.st_mtime) if newest is not None else stat.st_mtime
        except Exception:
            continue
    if newest is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(newest, tz=timezone.utc).isoformat()


def _map_base_status(value: str) -> str:
    token = str(value or "").strip().lower()
    if token == "completed":
        return "completed"
    if token == "unknown":
        return "missing"
    if token == "missing":
        return "pending"
    return "missing"


def _pipeline_triggered_status(meeting_id: str) -> str:
    intake_meta = _safe_read_json_file(_metadata_dir(meeting_id) / "intake.json")
    if str(intake_meta.get("status", "")).strip().lower() == "intake_completed":
        return "completed"
    if _meeting_dir(meeting_id).exists():
        return "running"
    return "pending"


def _temporal_status(meeting_id: str, stages: dict[str, str]) -> str:
    temporal_meta_path = _meeting_dir(meeting_id) / "temporal" / "temporal_metadata.json"
    temporal_meta = _safe_read_json_file(temporal_meta_path)
    if temporal_meta:
        token = str(temporal_meta.get("status", "")).strip().lower()
        if token == "completed":
            return "completed"
        if token in {"failed", "blocked"}:
            return "failed"
        return "missing"

    if stages.get("decision") == "completed":
        return "pending"
    return "pending"


def _calendar_status(meeting_id: str, stages: dict[str, str]) -> str:
    meeting_dir = _meeting_dir(meeting_id)
    calendar_meta_path = meeting_dir / "calendar" / "calendar_candidate_metadata.json"
    calendar_candidates_path = meeting_dir / "calendar" / "calendar_candidates.json"
    meta = _safe_read_json_file(calendar_meta_path)
    if meta:
        if meta.get("validation_passed") is False:
            return "failed"
        generated = str(meta.get("generated_at", "")).strip()
        if generated:
            return "completed"
        if "candidate_count" in meta:
            return "completed"
        return "missing"

    candidates = _safe_read_json_file(calendar_candidates_path)
    if candidates:
        rows = candidates.get("candidates", [])
        if isinstance(rows, list):
            return "completed"
        return "missing"

    if stages.get("temporal") == "completed":
        return "pending"
    return "pending"


def _report_status(meeting_id: str, stages: dict[str, str]) -> str:
    report_dir = _meeting_dir(meeting_id) / config.REPORT_DIR_NAME
    report_meta_path = report_dir / config.REPORT_METADATA_FILE
    report_meta = _safe_read_json_file(report_meta_path)
    if report_meta:
        status = str(report_meta.get("status", "")).strip().lower()
        if status == "completed":
            return "completed"
        if status in {"failed", "blocked"}:
            return "failed"
        return "missing"

    if any((report_dir / name).exists() for name in [config.REPORT_PAYLOAD_FILE, config.REPORT_HTML_FILE, config.REPORT_PDF_FILE]):
        return "missing"
    if stages.get("calendar") == "completed":
        return "pending"
    return "pending"


def _derive_current_and_overall(stages: dict[str, str]) -> tuple[str, str | None, dict[str, str]]:
    normalized = {key: str(stages.get(key, "missing")).strip().lower() for key in STAGE_ORDER}
    for key in STAGE_ORDER:
        if normalized[key] not in ANDROID_STAGE_STATUSES:
            normalized[key] = "missing"

    for stage in STAGE_ORDER:
        if normalized[stage] == "failed":
            return "failed", stage, normalized

    if all(normalized[stage] == "completed" for stage in STAGE_ORDER):
        return "completed", None, normalized

    first_unresolved: str | None = None
    completed_seen = False
    for stage in STAGE_ORDER:
        state = normalized[stage]
        if state == "completed":
            completed_seen = True
            continue
        first_unresolved = stage
        break

    if first_unresolved is None:
        return "running", None, normalized

    if normalized[first_unresolved] == "pending" and completed_seen:
        normalized[first_unresolved] = "running"
        return "running", first_unresolved, normalized

    if completed_seen:
        return "running", first_unresolved, normalized
    return "pending", first_unresolved, normalized


def get_processing_status(meeting_id: str) -> dict[str, Any]:
    meeting_key = str(meeting_id).strip()
    base = compute_stage_status(meeting_key)

    stages: dict[str, str] = {
        "pipeline_triggered": _pipeline_triggered_status(meeting_key),
        "normalization": _map_base_status(base.get("normalization", "missing")),
        "transcription": _map_base_status(base.get("transcription", "missing")),
        "cleanup": _map_base_status(base.get("cleanup", "missing")),
        "intelligence": _map_base_status(base.get("intelligence", "missing")),
        "executive": _map_base_status(base.get("executive", "missing")),
        "decision": _map_base_status(base.get("decision", "missing")),
    }
    stages["temporal"] = _temporal_status(meeting_key, stages)
    stages["calendar"] = _calendar_status(meeting_key, stages)
    stages["report"] = _report_status(meeting_key, stages)

    overall_status, current_stage, resolved_stages = _derive_current_and_overall(stages)
    return {
        "meeting_id": meeting_key,
        "overall_status": overall_status,
        "current_stage": current_stage,
        "stages": {key: resolved_stages.get(key, "missing") for key in STAGE_ORDER},
        "updated_at": _latest_updated_at(meeting_key),
    }

