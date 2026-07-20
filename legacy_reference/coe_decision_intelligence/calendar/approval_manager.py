# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

from typing import Any

from app.services.calendar.schemas import ApprovalState, CalendarCandidate, SyncStatus
from app.services.calendar.storage import (
    append_approval_log,
    build_metadata,
    load_candidates,
    load_metadata,
    save_candidates,
    save_metadata,
)
from app.services.calendar.utils import now_iso


def _find_candidate(candidates: list[CalendarCandidate], candidate_id: str) -> CalendarCandidate | None:
    cid = str(candidate_id).strip()
    for candidate in candidates:
        if candidate.candidate_id == cid:
            return candidate
    return None


def _touch_metadata(meeting_id: str, candidates: list[CalendarCandidate], generated_at: str) -> None:
    current_metadata = load_metadata(meeting_id)
    source_temporal_hash = str(current_metadata.get("source_temporal_hash", "")).strip()
    metadata = build_metadata(
        meeting_id=meeting_id,
        candidates=candidates,
        source_temporal_hash=source_temporal_hash,
        generated_at=generated_at,
    )
    save_metadata(meeting_id, metadata)


def _invalid_transition_response(meeting_id: str, candidate: CalendarCandidate, target: str) -> dict[str, Any]:
    return {
        "status": "error",
        "message": "invalid_transition",
        "meeting_id": meeting_id,
        "candidate_id": candidate.candidate_id,
        "current_approval_state": candidate.approval_state,
        "target_approval_state": target,
    }


def _log_state_change(
    meeting_id: str,
    candidate_id: str,
    actor: str,
    source: str,
    old_state: str,
    new_state: str,
    reason: str,
    timestamp: str,
) -> None:
    append_approval_log(
        meeting_id,
        {
            "timestamp": timestamp,
            "actor": actor,
            "source": source,
            "candidate_id": candidate_id,
            "old_approval_state": old_state,
            "new_approval_state": new_state,
            "reason": reason,
            "meeting_id": meeting_id,
        },
    )


def approve_candidate(
    meeting_id: str,
    candidate_id: str,
    actor: str,
    source: str,
    note: str = "",
) -> dict[str, Any]:
    meeting_key = str(meeting_id).strip()
    now_ts = now_iso()
    candidates = load_candidates(meeting_key)
    candidate = _find_candidate(candidates, candidate_id)
    if candidate is None:
        return {"status": "error", "message": "candidate_not_found", "meeting_id": meeting_key, "candidate_id": str(candidate_id).strip()}

    if candidate.approval_state != ApprovalState.PENDING.value:
        return _invalid_transition_response(meeting_key, candidate, ApprovalState.APPROVED.value)

    actor_name = str(actor or "unknown").strip()
    source_name = str(source or "dashboard_ui").strip()
    old_state = candidate.approval_state
    candidate.approval_state = ApprovalState.APPROVED.value
    candidate.sync_status = SyncStatus.QUEUED.value
    candidate.approval_source = source_name
    candidate.approved_by = actor_name
    candidate.approved_at = now_ts
    candidate.rejected_by = ""
    candidate.rejected_at = ""
    candidate.external_event_id = ""
    candidate.external_calendar_id = ""
    candidate.last_sync_error = ""
    candidate.approval_note = str(note or "").strip()
    candidate.updated_at = now_ts

    save_candidates(meeting_key, candidates)
    _touch_metadata(meeting_key, candidates, now_ts)
    _log_state_change(
        meeting_id=meeting_key,
        candidate_id=candidate.candidate_id,
        actor=actor_name,
        source=source_name,
        old_state=old_state,
        new_state=candidate.approval_state,
        reason=candidate.approval_note or "approved",
        timestamp=now_ts,
    )
    return {
        "status": "ok",
        "meeting_id": meeting_key,
        "candidate_id": candidate.candidate_id,
        "approval_state": candidate.approval_state,
        "sync_status": candidate.sync_status,
    }


def reject_candidate(
    meeting_id: str,
    candidate_id: str,
    actor: str,
    source: str,
    note: str = "",
) -> dict[str, Any]:
    meeting_key = str(meeting_id).strip()
    now_ts = now_iso()
    candidates = load_candidates(meeting_key)
    candidate = _find_candidate(candidates, candidate_id)
    if candidate is None:
        return {"status": "error", "message": "candidate_not_found", "meeting_id": meeting_key, "candidate_id": str(candidate_id).strip()}

    if candidate.approval_state != ApprovalState.PENDING.value:
        return _invalid_transition_response(meeting_key, candidate, ApprovalState.REJECTED.value)

    actor_name = str(actor or "unknown").strip()
    source_name = str(source or "dashboard_ui").strip()
    old_state = candidate.approval_state
    candidate.approval_state = ApprovalState.REJECTED.value
    candidate.approval_source = source_name
    candidate.rejected_by = actor_name
    candidate.rejected_at = now_ts
    candidate.approved_by = ""
    candidate.approved_at = ""
    candidate.approval_note = str(note or "").strip()
    candidate.updated_at = now_ts

    save_candidates(meeting_key, candidates)
    _touch_metadata(meeting_key, candidates, now_ts)
    _log_state_change(
        meeting_id=meeting_key,
        candidate_id=candidate.candidate_id,
        actor=actor_name,
        source=source_name,
        old_state=old_state,
        new_state=candidate.approval_state,
        reason=candidate.approval_note or "rejected",
        timestamp=now_ts,
    )
    return {
        "status": "ok",
        "meeting_id": meeting_key,
        "candidate_id": candidate.candidate_id,
        "approval_state": candidate.approval_state,
        "sync_status": candidate.sync_status,
    }


def reset_candidate_to_pending(
    meeting_id: str,
    candidate_id: str,
    actor: str,
    source: str,
    note: str = "",
) -> dict[str, Any]:
    meeting_key = str(meeting_id).strip()
    now_ts = now_iso()
    candidates = load_candidates(meeting_key)
    candidate = _find_candidate(candidates, candidate_id)
    if candidate is None:
        return {"status": "error", "message": "candidate_not_found", "meeting_id": meeting_key, "candidate_id": str(candidate_id).strip()}

    if candidate.approval_state == ApprovalState.PENDING.value:
        return _invalid_transition_response(meeting_key, candidate, ApprovalState.PENDING.value)
    if candidate.approval_state not in {ApprovalState.APPROVED.value, ApprovalState.REJECTED.value}:
        return _invalid_transition_response(meeting_key, candidate, ApprovalState.PENDING.value)

    actor_name = str(actor or "unknown").strip()
    source_name = str(source or "dashboard_ui").strip()
    old_state = candidate.approval_state
    candidate.approval_state = ApprovalState.PENDING.value
    candidate.approval_source = source_name
    candidate.approved_by = ""
    candidate.approved_at = ""
    candidate.rejected_by = ""
    candidate.rejected_at = ""
    candidate.approval_note = str(note or "").strip()
    candidate.updated_at = now_ts

    save_candidates(meeting_key, candidates)
    _touch_metadata(meeting_key, candidates, now_ts)
    _log_state_change(
        meeting_id=meeting_key,
        candidate_id=candidate.candidate_id,
        actor=actor_name,
        source=source_name,
        old_state=old_state,
        new_state=candidate.approval_state,
        reason=candidate.approval_note or "reset_to_pending",
        timestamp=now_ts,
    )
    return {
        "status": "ok",
        "meeting_id": meeting_key,
        "candidate_id": candidate.candidate_id,
        "approval_state": candidate.approval_state,
        "sync_status": candidate.sync_status,
    }
