# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class CandidateState(str, Enum):
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SyncStatus(str, Enum):
    NOT_QUEUED = "not_queued"
    QUEUED = "queued"
    SYNCED = "synced"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class CalendarCandidate:
    candidate_id: str
    meeting_id: str
    temporal_item_id: str
    source_temporal_fingerprint: str
    type: str
    title: str
    summary: str
    normalized_time: dict[str, Any]
    all_day: bool
    timezone: str
    confidence: str
    certainty_class: str
    support_level: str
    evidence_span: str
    source_artifacts: list[str]
    blockers: list[str]
    recommended_action: str
    eligibility_status: str
    candidate_state: str
    approval_state: str
    sync_status: str
    approval_source: str
    approved_by: str
    approved_at: str
    rejected_by: str
    rejected_at: str
    approval_note: str
    created_at: str
    updated_at: str
    candidate_version: int
    candidate_hash: str
    display_date: str
    display_time: str
    display_reason: str
    dedup_key: str = ""
    canonical_candidate_id: str = ""
    external_event_id: str = ""
    external_calendar_id: str = ""
    last_sync_at: str = ""
    last_sync_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["eligibility_status"] = str(self.eligibility_status)
        payload["candidate_state"] = str(self.candidate_state)
        payload["approval_state"] = str(self.approval_state)
        payload["sync_status"] = str(self.sync_status)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalendarCandidate":
        payload = dict(data) if isinstance(data, dict) else {}
        return cls(
            candidate_id=str(payload.get("candidate_id", "")).strip(),
            meeting_id=str(payload.get("meeting_id", "")).strip(),
            temporal_item_id=str(payload.get("temporal_item_id", "")).strip(),
            source_temporal_fingerprint=str(payload.get("source_temporal_fingerprint", "")).strip(),
            type=str(payload.get("type", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            normalized_time=payload.get("normalized_time", {}) if isinstance(payload.get("normalized_time"), dict) else {},
            all_day=bool(payload.get("all_day", False)),
            timezone=str(payload.get("timezone", "unknown")).strip() or "unknown",
            confidence=str(payload.get("confidence", "")).strip(),
            certainty_class=str(payload.get("certainty_class", "")).strip(),
            support_level=str(payload.get("support_level", "")).strip(),
            evidence_span=str(payload.get("evidence_span", "")).strip(),
            source_artifacts=list(payload.get("source_artifacts", [])) if isinstance(payload.get("source_artifacts"), list) else [],
            blockers=list(payload.get("blockers", [])) if isinstance(payload.get("blockers"), list) else [],
            recommended_action=str(payload.get("recommended_action", "")).strip(),
            eligibility_status=str(payload.get("eligibility_status", EligibilityStatus.BLOCKED.value)).strip() or EligibilityStatus.BLOCKED.value,
            candidate_state=str(payload.get("candidate_state", CandidateState.BLOCKED.value)).strip() or CandidateState.BLOCKED.value,
            approval_state=str(payload.get("approval_state", ApprovalState.PENDING.value)).strip() or ApprovalState.PENDING.value,
            sync_status=str(payload.get("sync_status", SyncStatus.NOT_QUEUED.value)).strip() or SyncStatus.NOT_QUEUED.value,
            approval_source=str(payload.get("approval_source", "")).strip(),
            approved_by=str(payload.get("approved_by", "")).strip(),
            approved_at=str(payload.get("approved_at", "")).strip(),
            rejected_by=str(payload.get("rejected_by", "")).strip(),
            rejected_at=str(payload.get("rejected_at", "")).strip(),
            approval_note=str(payload.get("approval_note", "")).strip(),
            created_at=str(payload.get("created_at", "")).strip(),
            updated_at=str(payload.get("updated_at", "")).strip(),
            candidate_version=int(payload.get("candidate_version", 1) or 1),
            candidate_hash=str(payload.get("candidate_hash", "")).strip(),
            display_date=str(payload.get("display_date", "")).strip(),
            display_time=str(payload.get("display_time", "")).strip(),
            display_reason=str(payload.get("display_reason", "")).strip(),
            dedup_key=str(payload.get("dedup_key", "")).strip(),
            canonical_candidate_id=str(payload.get("canonical_candidate_id", "")).strip(),
            external_event_id=str(payload.get("external_event_id", "")).strip(),
            external_calendar_id=str(payload.get("external_calendar_id", "")).strip(),
            last_sync_at=str(payload.get("last_sync_at", "")).strip(),
            last_sync_error=str(payload.get("last_sync_error", "")).strip(),
        )


@dataclass
class CalendarCandidateMetadata:
    meeting_id: str
    generated_at: str
    source_temporal_hash: str
    candidate_count: int
    eligible_count: int
    pending_count: int
    approved_count: int
    rejected_count: int
    blocked_count: int
    validation_passed: bool
    suppressed_duplicates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalLogEntry:
    timestamp: str
    actor: str
    source: str
    candidate_id: str
    old_approval_state: str
    new_approval_state: str
    reason: str
    meeting_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
