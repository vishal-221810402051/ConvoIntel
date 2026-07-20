# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from app.config import config
from app.services.calendar.schemas import (
    ApprovalLogEntry,
    ApprovalState,
    CalendarCandidate,
    CalendarCandidateMetadata,
    EligibilityStatus,
    SyncStatus,
)
from app.services.calendar.utils import now_iso


CALENDAR_DIR_NAME = "calendar"
CANDIDATES_FILE_NAME = "calendar_candidates.json"
METADATA_FILE_NAME = "calendar_candidate_metadata.json"
APPROVAL_LOG_FILE_NAME = "calendar_approval_log.jsonl"
SYNC_LOG_FILE_NAME = "calendar_sync_log.json"


def _calendar_dir(meeting_id: str) -> Path:
    return config.PROCESSED_PATH / str(meeting_id).strip() / CALENDAR_DIR_NAME


def _candidates_path(meeting_id: str) -> Path:
    return _calendar_dir(meeting_id) / CANDIDATES_FILE_NAME


def _metadata_path(meeting_id: str) -> Path:
    return _calendar_dir(meeting_id) / METADATA_FILE_NAME


def _approval_log_path(meeting_id: str) -> Path:
    return _calendar_dir(meeting_id) / APPROVAL_LOG_FILE_NAME


def _sync_log_path(meeting_id: str) -> Path:
    return _calendar_dir(meeting_id) / SYNC_LOG_FILE_NAME


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def load_candidates(meeting_id: str) -> list[CalendarCandidate]:
    payload = _safe_read_json(_candidates_path(meeting_id))
    rows = payload.get("candidates", [])
    if not isinstance(rows, list):
        return []
    out: list[CalendarCandidate] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(CalendarCandidate.from_dict(row))
    return out


def _candidate_to_dict(candidate: CalendarCandidate | dict[str, Any]) -> dict[str, Any]:
    if isinstance(candidate, CalendarCandidate):
        return candidate.to_dict()
    if isinstance(candidate, dict):
        return CalendarCandidate.from_dict(candidate).to_dict()
    return {}


def load_candidates_payload(meeting_id: str) -> dict[str, Any]:
    payload = _safe_read_json(_candidates_path(meeting_id))
    rows = payload.get("candidates", [])
    if not isinstance(rows, list):
        rows = []
    normalized = [_candidate_to_dict(row) for row in rows if isinstance(row, dict)]
    normalized.sort(
        key=lambda row: (
            str(row.get("display_date", "")).strip(),
            str(row.get("type", "")).strip(),
            str(row.get("candidate_id", "")).strip(),
        )
    )
    return {
        "meeting_id": str(meeting_id).strip(),
        "candidates": normalized,
    }


def save_candidates(meeting_id: str, candidates: Iterable[CalendarCandidate | dict[str, Any]]) -> Path:
    cdir = _calendar_dir(meeting_id)
    cdir.mkdir(parents=True, exist_ok=True)
    rows = [_candidate_to_dict(candidate) for candidate in candidates]
    rows = [row for row in rows if isinstance(row, dict) and row]
    rows.sort(
        key=lambda row: (
            str(row.get("display_date", "")).strip(),
            str(row.get("type", "")).strip(),
            str(row.get("candidate_id", "")).strip(),
        )
    )
    payload = {
        "meeting_id": str(meeting_id).strip(),
        "candidates": rows,
    }
    path = _candidates_path(meeting_id)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_metadata(meeting_id: str) -> dict[str, Any]:
    return _safe_read_json(_metadata_path(meeting_id))


def build_metadata(
    meeting_id: str,
    candidates: list[CalendarCandidate],
    source_temporal_hash: str,
    generated_at: str | None = None,
    suppressed_duplicates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    generated_ts = generated_at or now_iso()
    eligible_count = sum(1 for row in candidates if row.eligibility_status == EligibilityStatus.ELIGIBLE.value)
    blocked_count = sum(1 for row in candidates if row.eligibility_status == EligibilityStatus.BLOCKED.value)
    pending_count = sum(1 for row in candidates if row.approval_state == ApprovalState.PENDING.value)
    approved_count = sum(1 for row in candidates if row.approval_state == ApprovalState.APPROVED.value)
    rejected_count = sum(1 for row in candidates if row.approval_state == ApprovalState.REJECTED.value)
    allowed_sync = {status.value for status in SyncStatus}
    validation_passed = all(
        bool(row.candidate_id)
        and bool(row.temporal_item_id)
        and str(row.sync_status).strip() in allowed_sync
        for row in candidates
    )
    metadata = CalendarCandidateMetadata(
        meeting_id=str(meeting_id).strip(),
        generated_at=generated_ts,
        source_temporal_hash=str(source_temporal_hash or "").strip(),
        candidate_count=len(candidates),
        eligible_count=eligible_count,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        blocked_count=blocked_count,
        validation_passed=validation_passed,
        suppressed_duplicates=[dict(row) for row in (suppressed_duplicates or []) if isinstance(row, dict)],
    )
    return metadata.to_dict()


def save_metadata(meeting_id: str, metadata: dict[str, Any]) -> Path:
    cdir = _calendar_dir(meeting_id)
    cdir.mkdir(parents=True, exist_ok=True)
    path = _metadata_path(meeting_id)
    payload = dict(metadata) if isinstance(metadata, dict) else {}
    payload["meeting_id"] = str(meeting_id).strip()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def append_approval_log(meeting_id: str, log_entry: dict[str, Any]) -> Path:
    cdir = _calendar_dir(meeting_id)
    cdir.mkdir(parents=True, exist_ok=True)
    path = _approval_log_path(meeting_id)
    raw = dict(log_entry) if isinstance(log_entry, dict) else {}
    entry = ApprovalLogEntry(
        timestamp=str(raw.get("timestamp", now_iso())).strip() or now_iso(),
        actor=str(raw.get("actor", "unknown")).strip() or "unknown",
        source=str(raw.get("source", "dashboard_ui")).strip() or "dashboard_ui",
        candidate_id=str(raw.get("candidate_id", "")).strip(),
        old_approval_state=str(raw.get("old_approval_state", "")).strip(),
        new_approval_state=str(raw.get("new_approval_state", "")).strip(),
        reason=str(raw.get("reason", "")).strip(),
        meeting_id=str(raw.get("meeting_id", str(meeting_id).strip())).strip(),
    )
    payload = entry.to_dict()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def load_sync_log(meeting_id: str) -> list[dict[str, Any]]:
    path = _sync_log_path(meeting_id)
    if not path.exists() or not path.is_file():
        return []
    payload = _safe_read_json(path)
    rows = payload.get("items", [])
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
    return out


def save_sync_log(meeting_id: str, rows: list[dict[str, Any]]) -> Path:
    cdir = _calendar_dir(meeting_id)
    cdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meeting_id": str(meeting_id).strip(),
        "items": [dict(row) for row in rows if isinstance(row, dict)],
    }
    path = _sync_log_path(meeting_id)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def update_candidate(meeting_id: str, candidate_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    payload = load_candidates_payload(meeting_id)
    rows = payload.get("candidates", [])
    cid = str(candidate_id).strip()
    update_values = dict(updates) if isinstance(updates, dict) else {}

    updated: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("candidate_id", "")).strip() != cid:
            continue
        row.update(update_values)
        updated = CalendarCandidate.from_dict(row).to_dict()
        row.clear()
        row.update(updated)
        break

    if updated is None:
        raise ValueError(f"Candidate not found: {cid}")

    save_candidates(meeting_id, rows)
    return updated


def calendar_paths(meeting_id: str) -> dict[str, Path]:
    return {
        "base": _calendar_dir(meeting_id),
        "calendar_dir": _calendar_dir(meeting_id),
        "candidates": _candidates_path(meeting_id),
        "metadata": _metadata_path(meeting_id),
        "approval_log": _approval_log_path(meeting_id),
        "sync_log": _sync_log_path(meeting_id),
        "candidates_path": _candidates_path(meeting_id),
        "metadata_path": _metadata_path(meeting_id),
        "approval_log_path": _approval_log_path(meeting_id),
    }
