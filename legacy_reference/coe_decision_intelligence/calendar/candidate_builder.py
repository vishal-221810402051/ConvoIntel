# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import config
from app.services.calendar.schemas import (
    ApprovalState,
    CandidateState,
    CalendarCandidate,
    EligibilityStatus,
    SyncStatus,
)
from app.services.calendar.utils import compute_candidate_hash


def _temporal_path(meeting_id: str) -> Path:
    return config.PROCESSED_PATH / str(meeting_id).strip() / "temporal" / "temporal_intelligence.json"


def load_temporal_payload(meeting_id: str) -> dict[str, Any]:
    path = _temporal_path(meeting_id)
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = _safe_text(value)
        if text and text not in out:
            out.append(text)
    return out


def _canonical_time(normalized_time: dict[str, Any]) -> str:
    ntype = _safe_text(normalized_time.get("type", "")).lower()
    nvalue = _safe_text(normalized_time.get("value", ""))
    tod = _safe_text(normalized_time.get("time_of_day", ""))
    return f"{ntype}|{nvalue}|{tod}"


def _candidate_identity_seed(meeting_id: str, temporal_item_id: str, temporal_type: str, normalized_time: dict[str, Any]) -> str:
    return f"{meeting_id}|{temporal_item_id}|{_safe_text(temporal_type).lower()}|{_canonical_time(normalized_time)}"


def _candidate_id(meeting_id: str, temporal_item_id: str, temporal_type: str, normalized_time: dict[str, Any]) -> str:
    seed = _candidate_identity_seed(meeting_id, temporal_item_id, temporal_type, normalized_time)
    return f"CC-{compute_candidate_hash({'seed': seed})[:16]}"


def _source_temporal_fingerprint(item: dict[str, Any]) -> str:
    return compute_candidate_hash(item)


def _display_fields(normalized_time: dict[str, Any], temporal_type: str) -> tuple[str, str, str, bool, str]:
    ntype = _safe_text(normalized_time.get("type", "")).lower()
    nvalue = _safe_text(normalized_time.get("value", ""))
    tod = _safe_text(normalized_time.get("time_of_day", ""))
    timezone = _safe_text(normalized_time.get("timezone", "unknown")) or "unknown"

    if ntype == "exact_datetime":
        date_part = nvalue
        time_part = tod
        if "t" in nvalue.lower():
            split = re.split(r"[Tt]", nvalue, maxsplit=1)
            if len(split) == 2:
                date_part = _safe_text(split[0]) or nvalue
                raw_time = _safe_text(split[1])
                if raw_time:
                    time_part = raw_time[:5] if len(raw_time) >= 5 else raw_time
        return date_part, (time_part or "Time set"), "Exact date/time signal.", False, timezone
    if ntype == "exact_date":
        if tod:
            return nvalue, tod, "Exact date/time signal.", False, timezone
        return nvalue, "All day", "Exact date signal without explicit time.", True, timezone
    if ntype == "date_range":
        return nvalue, "Window", "Date range signal requires review.", True, timezone
    if ntype == "relative_time":
        return nvalue, "Relative", "Relative time expression requires review.", True, timezone
    return nvalue or "Unresolved", "Unresolved", "Unresolved temporal expression.", True, timezone


def _map_eligibility(temporal_type: str, calendar_ready: bool) -> tuple[str, str]:
    ttype = _safe_text(temporal_type).lower()
    if ttype in {"meeting", "follow_up"}:
        if calendar_ready:
            return EligibilityStatus.ELIGIBLE.value, CandidateState.DRAFT.value
        return EligibilityStatus.BLOCKED.value, CandidateState.BLOCKED.value
    if ttype == "deadline":
        if calendar_ready:
            return EligibilityStatus.ELIGIBLE.value, CandidateState.DRAFT.value
        return EligibilityStatus.REVIEW_REQUIRED.value, CandidateState.REVIEW_REQUIRED.value
    if ttype in {"reminder", "time_window"}:
        return EligibilityStatus.REVIEW_REQUIRED.value, CandidateState.REVIEW_REQUIRED.value
    if ttype in {"tentative_date", "milestone", "unresolved_temporal_question"}:
        return EligibilityStatus.BLOCKED.value, CandidateState.BLOCKED.value
    return EligibilityStatus.BLOCKED.value, CandidateState.BLOCKED.value


def _default_blocker(temporal_type: str) -> str:
    mapping = {
        "tentative_date": "tentative_date_not_syncable",
        "milestone": "milestone_requires_manual_translation",
        "unresolved_temporal_question": "unresolved_temporal_question",
        "meeting": "meeting_not_calendar_ready",
        "follow_up": "follow_up_not_calendar_ready",
    }
    return mapping.get(_safe_text(temporal_type).lower(), "manual_review_required")


def _candidate_summary(item: dict[str, Any]) -> str:
    intent = _safe_text(item.get("intent", ""))
    raw_reference = _safe_text(item.get("raw_reference", ""))
    evidence = _safe_text(item.get("evidence_span", ""))
    if intent and raw_reference:
        return f"{intent} ({raw_reference})"
    if intent:
        return intent
    if raw_reference:
        return raw_reference
    return evidence[:220]


def _candidate_content_payload(candidate: CalendarCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "meeting_id": candidate.meeting_id,
        "temporal_item_id": candidate.temporal_item_id,
        "source_temporal_fingerprint": candidate.source_temporal_fingerprint,
        "type": candidate.type,
        "title": candidate.title,
        "summary": candidate.summary,
        "normalized_time": candidate.normalized_time,
        "all_day": candidate.all_day,
        "timezone": candidate.timezone,
        "confidence": candidate.confidence,
        "certainty_class": candidate.certainty_class,
        "support_level": candidate.support_level,
        "evidence_span": candidate.evidence_span,
        "source_artifacts": sorted(candidate.source_artifacts),
        "blockers": sorted(candidate.blockers),
        "recommended_action": candidate.recommended_action,
        "eligibility_status": candidate.eligibility_status,
        "candidate_state": candidate.candidate_state,
        "display_date": candidate.display_date,
        "display_time": candidate.display_time,
        "display_reason": candidate.display_reason,
    }


def _build_candidate(meeting_id: str, item: dict[str, Any]) -> CalendarCandidate | None:
    temporal_item_id = _safe_text(item.get("item_id", ""))
    if not temporal_item_id:
        return None
    temporal_type = _safe_text(item.get("type", "")).lower()
    if not temporal_type:
        return None

    normalized_time = item.get("normalized_time", {})
    if not isinstance(normalized_time, dict):
        normalized_time = {}
    calendar_ready = bool(item.get("calendar_ready", False))

    eligibility_status, candidate_state = _map_eligibility(temporal_type, calendar_ready)
    blockers = _safe_list(item.get("calendar_blockers", []))
    if eligibility_status == EligibilityStatus.BLOCKED.value and not blockers:
        blockers = [_default_blocker(temporal_type)]

    display_date, display_time, display_reason, all_day, timezone = _display_fields(normalized_time, temporal_type)
    candidate = CalendarCandidate(
        candidate_id=_candidate_id(
            meeting_id=meeting_id,
            temporal_item_id=temporal_item_id,
            temporal_type=temporal_type,
            normalized_time=normalized_time,
        ),
        meeting_id=meeting_id,
        temporal_item_id=temporal_item_id,
        source_temporal_fingerprint=_source_temporal_fingerprint(item),
        type=temporal_type,
        title=_safe_text(item.get("title", "")) or f"Calendar Candidate: {_safe_text(item.get('intent', ''))}",
        summary=_candidate_summary(item),
        normalized_time=normalized_time,
        all_day=all_day,
        timezone=timezone,
        confidence=_safe_text(item.get("confidence", "low")).lower() or "low",
        certainty_class=_safe_text(item.get("certainty_class", "uncertain")).lower() or "uncertain",
        support_level=_safe_text(item.get("support_level", "DIRECTLY_SUPPORTED")) or "DIRECTLY_SUPPORTED",
        evidence_span=_safe_text(item.get("evidence_span", "")),
        source_artifacts=_safe_list(item.get("source_artifacts", [])),
        blockers=blockers,
        recommended_action=_safe_text(item.get("recommended_action", "")),
        eligibility_status=eligibility_status,
        candidate_state=candidate_state,
        approval_state=ApprovalState.PENDING.value,
        sync_status=SyncStatus.NOT_QUEUED.value,
        approval_source="",
        approved_by="",
        approved_at="",
        rejected_by="",
        rejected_at="",
        approval_note="",
        created_at="",
        updated_at="",
        candidate_version=1,
        candidate_hash="",
        display_date=display_date,
        display_time=display_time,
        display_reason=display_reason,
    )
    candidate.candidate_hash = compute_candidate_hash(_candidate_content_payload(candidate))
    return candidate


def build_calendar_candidates(meeting_id: str) -> list[CalendarCandidate]:
    meeting_key = _safe_text(meeting_id)
    temporal_payload = load_temporal_payload(meeting_key)
    rows = temporal_payload.get("items", [])
    if not isinstance(rows, list):
        return []

    out: list[CalendarCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _build_candidate(meeting_key, row)
        if candidate is not None:
            out.append(candidate)

    out.sort(key=lambda row: (row.display_date, row.type, row.title, row.candidate_id))
    return out
