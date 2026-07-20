# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.calendar.schemas import CalendarCandidate
from app.services.calendar.utils import resolve_timezone


def _candidate_to_dict(candidate: CalendarCandidate | dict[str, Any]) -> dict[str, Any]:
    if isinstance(candidate, CalendarCandidate):
        return candidate.to_dict()
    if isinstance(candidate, dict):
        return dict(candidate)
    return {}


def _intent_bucket(candidate_type: str) -> str:
    token = str(candidate_type or "").strip().lower()
    if token in {"meeting", "follow_up"}:
        return "meeting_like"
    return token


def compute_dedup_key(candidate: CalendarCandidate | dict[str, Any]) -> str:
    row = _candidate_to_dict(candidate)
    normalized = row.get("normalized_time", {})
    if not isinstance(normalized, dict):
        normalized = {}

    meeting_id = str(row.get("meeting_id", "")).strip()
    nt_type = str(normalized.get("type", "")).strip()
    nt_value = str(normalized.get("value", "")).strip()
    all_day = bool(row.get("all_day", False))
    intent_bucket = _intent_bucket(str(row.get("type", "")).strip())
    timezone_name = resolve_timezone(row)

    return f"{meeting_id}|{intent_bucket}|{nt_type}|{nt_value}|{all_day}|{timezone_name}"


def _safe_int_candidate_id(candidate_id: str) -> int:
    token = str(candidate_id or "").strip().replace("CC-", "")
    try:
        return int(token, 16)
    except Exception:
        return 0


def _score(candidate: CalendarCandidate) -> tuple[int, int, int, int, int, int, int, int]:
    normalized = candidate.normalized_time if isinstance(candidate.normalized_time, dict) else {}
    nt_type = str(normalized.get("type", "")).strip().lower()

    nt_type_order = {
        "exact_datetime": 5,
        "exact_date": 4,
        "date_range": 3,
        "relative_time": 2,
        "unresolved_text": 1,
    }
    eligibility_order = {"eligible": 3, "review_required": 2, "blocked": 1}
    confidence_order = {"high": 3, "medium": 2, "low": 1}
    support_order = {"DIRECTLY_SUPPORTED": 3, "ACCEPTABLE_INFERENCE": 2, "WEAK_INFERENCE": 1}
    sync_order = {"synced": 4, "queued": 3, "failed": 2, "not_queued": 1, "skipped": 0}

    return (
        1 if str(candidate.external_event_id).strip() else 0,
        sync_order.get(str(candidate.sync_status), 0),
        eligibility_order.get(str(candidate.eligibility_status), 0),
        nt_type_order.get(nt_type, 0),
        confidence_order.get(str(candidate.confidence), 0),
        support_order.get(str(candidate.support_level), 0),
        len(str(candidate.evidence_span or "")),
        -_safe_int_candidate_id(candidate.candidate_id),
    )


def select_canonical(candidates: list[CalendarCandidate]) -> CalendarCandidate:
    ordered = sorted(candidates, key=_score, reverse=True)
    return ordered[0]


def deduplicate_candidates(
    candidates: list[CalendarCandidate],
) -> tuple[list[CalendarCandidate], list[dict[str, Any]]]:
    groups: dict[str, list[CalendarCandidate]] = defaultdict(list)
    for candidate in candidates:
        key = compute_dedup_key(candidate)
        candidate.dedup_key = key
        groups[key].append(candidate)

    canonical_rows: list[CalendarCandidate] = []
    suppressed: list[dict[str, Any]] = []

    for key in sorted(groups.keys()):
        rows = groups[key]
        winner = select_canonical(rows)
        winner.canonical_candidate_id = winner.candidate_id
        winner.dedup_key = key
        canonical_rows.append(winner)

        suppressed_ids: list[str] = []
        for row in rows:
            if row.candidate_id == winner.candidate_id:
                continue
            row.canonical_candidate_id = winner.candidate_id
            row.dedup_key = key
            suppressed_ids.append(row.candidate_id)
            suppressed.append(
                {
                    "suppressed_candidate_id": row.candidate_id,
                    "canonical_candidate_id": winner.candidate_id,
                    "dedup_key": key,
                    "reason": "duplicate_same_event",
                    "source_artifacts": sorted(set(str(x) for x in row.source_artifacts if str(x).strip())),
                    "title": row.title,
                    "summary": row.summary,
                    "sync_status": row.sync_status,
                    "external_event_id": row.external_event_id,
                }
            )

        if suppressed_ids:
            print("[DEDUP]")
            print(f"key: {key}")
            print(f"canonical: {winner.candidate_id}")
            print(f"suppressed: {suppressed_ids}")

    canonical_rows.sort(
        key=lambda row: (
            row.display_date,
            row.type,
            row.title,
            row.candidate_id,
        )
    )
    suppressed.sort(
        key=lambda row: (
            str(row.get("dedup_key", "")).strip(),
            str(row.get("suppressed_candidate_id", "")).strip(),
        )
    )
    return canonical_rows, suppressed
