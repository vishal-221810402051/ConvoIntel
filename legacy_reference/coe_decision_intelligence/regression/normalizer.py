# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import app.config as app_config


def _sort_str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted([str(v).strip() for v in values if isinstance(v, str)], key=lambda x: x.lower())


def _normalize_evidence(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if isinstance(v, str) and str(v).strip()]


def normalize_intelligence_artifact(data: dict) -> dict:
    payload = data if isinstance(data, dict) else {}
    critical = {
        "meeting_context": payload.get("meeting_context", {}),
        "decisions": sorted(
            [
                {
                    "text": str(item.get("text", "")).strip(),
                    "confidence": str(item.get("confidence", "")).strip().lower(),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
                for item in payload.get("decisions", [])
                if isinstance(item, dict)
            ],
            key=lambda x: (x["text"].lower(), x["evidence"].lower()),
        ),
        "risks": sorted(
            [
                {
                    "text": str(item.get("text", "")).strip(),
                    "severity": str(item.get("severity", "")).strip().lower(),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
                for item in payload.get("risks", [])
                if isinstance(item, dict)
            ],
            key=lambda x: (x["text"].lower(), x["evidence"].lower()),
        ),
        "action_plan": sorted(
            [
                {
                    "task": str(item.get("task", "")).strip(),
                    "owner": str(item.get("owner", "")).strip(),
                    "priority": str(item.get("priority", "")).strip().lower(),
                    "status": str(item.get("status", "")).strip().lower(),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
                for item in payload.get("action_plan", [])
                if isinstance(item, dict)
            ],
            key=lambda x: (x["task"].lower(), x["owner"].lower(), x["evidence"].lower()),
        ),
        "roadmap": sorted(
            [
                {
                    "step_order": int(item.get("step_order", 0)),
                    "step": str(item.get("step", "")).strip(),
                    "time_horizon": str(item.get("time_horizon", "")).strip().lower(),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
                for item in payload.get("roadmap", [])
                if isinstance(item, dict)
            ],
            key=lambda x: (x["step_order"], x["step"].lower(), x["evidence"].lower()),
        ),
        "deadlines": sorted(
            [
                {
                    "event": str(item.get("event", "")).strip(),
                    "date": str(item.get("date", "")).strip(),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
                for item in payload.get("deadlines", [])
                if isinstance(item, dict)
            ],
            key=lambda x: (x["event"].lower(), x["date"].lower(), x["evidence"].lower()),
        ),
        "stakeholders": sorted(
            [
                {
                    "name": str(item.get("name", "")).strip(),
                    "role": str(item.get("role", "")).strip(),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
                for item in payload.get("stakeholders", [])
                if isinstance(item, dict)
            ],
            key=lambda x: (x["name"].lower(), x["role"].lower(), x["evidence"].lower()),
        ),
        "timeline_mentions": sorted(
            [
                {
                    "text": str(item.get("text", "")).strip(),
                    "raw_time_reference": str(item.get("raw_time_reference", "")).strip(),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
                for item in payload.get("timeline_mentions", [])
                if isinstance(item, dict)
            ],
            key=lambda x: (
                x["text"].lower(),
                x["raw_time_reference"].lower(),
                x["evidence"].lower(),
            ),
        ),
    }
    return {
        "critical": critical,
        "soft_text": {
            "summary": str(payload.get("summary", "")).strip(),
        },
    }


def normalize_executive_artifact(data: dict) -> dict:
    payload = data if isinstance(data, dict) else {}
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    power = payload.get("power_structure", {}) if isinstance(payload.get("power_structure"), dict) else {}
    critical = {
        "power_structure": {
            "sponsor": _sort_str_list(power.get("sponsor", [])),
            "strategic_authority": _sort_str_list(power.get("strategic_authority", [])),
            "decision_makers": _sort_str_list(power.get("decision_makers", [])),
            "advisors": _sort_str_list(power.get("advisors", [])),
            "executors": _sort_str_list(power.get("executors", [])),
            "implementation_owner": _sort_str_list(power.get("implementation_owner", [])),
            "unknown_authority_gaps": _sort_str_list(power.get("unknown_authority_gaps", [])),
            "confidence": str(power.get("confidence", "")).strip().lower(),
        },
        "execution_structure": payload.get("execution_structure", {}),
        "business_model_clarity": payload.get("business_model_clarity", {}),
        "risk_posture_overall": str(
            (payload.get("risk_posture", {}) if isinstance(payload.get("risk_posture"), dict) else {}).get("overall", "")
        ).strip().lower(),
        "role_clarity_assessment": sorted(
            [
                {
                    "actor": str(row.get("actor", "")).strip(),
                    "role": str(row.get("role", "")).strip(),
                    "authority_level": str(row.get("authority_level", "")).strip().lower(),
                    "responsibility_level": str(row.get("responsibility_level", "")).strip().lower(),
                    "clarity": str(row.get("clarity", "")).strip().lower(),
                    "evidence": str(row.get("evidence", "")).strip(),
                }
                for row in payload.get("role_clarity_assessment", [])
                if isinstance(row, dict)
            ],
            key=lambda x: (x["actor"].lower(), x["role"].lower()),
        ),
        "negotiation_flags": sorted(
            [
                {
                    "topic": str(row.get("topic", "")).strip(),
                    "status": str(row.get("status", "")).strip().lower(),
                    "severity": str(row.get("severity", "")).strip().lower(),
                    "evidence": str(row.get("evidence", "")).strip(),
                }
                for row in payload.get("negotiation_flags", [])
                if isinstance(row, dict)
            ],
            key=lambda x: (
                x["topic"].lower(),
                x["status"].lower(),
                x["severity"].lower(),
                x["evidence"].lower(),
            ),
        ),
        "executive_warnings": sorted(
            [
                {
                    "severity": str(row.get("severity", "")).strip().lower(),
                    "reason": str(row.get("reason", "")).strip(),
                    "evidence": str(row.get("evidence", "")).strip(),
                }
                for row in payload.get("executive_warnings", [])
                if isinstance(row, dict)
            ],
            key=lambda x: (x["severity"], x["reason"].lower(), x["evidence"].lower()),
        ),
    }
    soft_questions = sorted(
        [
            {
                "question": str(row.get("question", "")).strip(),
                "priority": str(row.get("priority", "")).strip().lower(),
                "why_now": str(row.get("why_now", "")).strip(),
            }
            for row in payload.get("recommended_next_questions", [])
            if isinstance(row, dict)
        ],
        key=lambda x: (
            priority_rank.get(x["priority"], 9),
            x["question"].lower(),
            x["why_now"].lower(),
        ),
    )
    return {
        "critical": critical,
        "soft_text": {
            "executive_summary": {
                "meaning_of_meeting": str(
                    (payload.get("executive_summary", {}) if isinstance(payload.get("executive_summary"), dict) else {}).get("meaning_of_meeting", "")
                ).strip(),
                "intent": str(
                    (payload.get("executive_summary", {}) if isinstance(payload.get("executive_summary"), dict) else {}).get("intent", "")
                ).strip(),
                "commitment": str(
                    (payload.get("executive_summary", {}) if isinstance(payload.get("executive_summary"), dict) else {}).get("commitment", "")
                ).strip(),
            },
            "strategic_objective": {
                "objective": str(
                    (payload.get("strategic_objective", {}) if isinstance(payload.get("strategic_objective"), dict) else {}).get("objective", "")
                ).strip(),
                "business_direction": str(
                    (payload.get("strategic_objective", {}) if isinstance(payload.get("strategic_objective"), dict) else {}).get("business_direction", "")
                ).strip(),
                "success_condition": str(
                    (payload.get("strategic_objective", {}) if isinstance(payload.get("strategic_objective"), dict) else {}).get("success_condition", "")
                ).strip(),
            },
            "recommended_next_questions": soft_questions,
        },
    }


def normalize_decision_artifact(data: dict) -> dict:
    payload = data if isinstance(data, dict) else {}
    records = payload.get("decision_records", [])
    if not isinstance(records, list):
        records = []

    def owner_key(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(item.get("ownership_type", "")).strip().lower(),
            str(item.get("actor", "")).strip().lower(),
        )

    def commitment_key(item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("actor", "")).strip().lower(),
            str(item.get("commitment_type", "")).strip().lower(),
            str(item.get("commitment", "")).strip().lower(),
        )

    def dependency_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(item.get("type", "")).strip().lower(),
            str(item.get("status", "")).strip().lower(),
            str(item.get("blocking_level", "")).strip().lower(),
            str(item.get("reason", "")).strip().lower(),
        )

    def gap_key(item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("gap_type", "")).strip().lower(),
            str(item.get("criticality", "")).strip().lower(),
            str(item.get("question", "")).strip().lower(),
        )

    def signal_key(item: dict[str, Any]) -> tuple[str, str]:
        return (
            str(item.get("signal_type", "")).strip().lower(),
            str(item.get("raw_reference", "")).strip().lower(),
        )

    normalized_records: list[dict[str, Any]] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        normalized_records.append(
            {
                "decision_id": str(row.get("decision_id", "")).strip(),
                "statement": str(row.get("statement", "")).strip(),
                "state": str(row.get("state", "")).strip().lower(),
                "impact_level": str(row.get("impact_level", "")).strip().lower(),
                "confidence": str(row.get("confidence", "")).strip().lower(),
                "primary_owner": str(row.get("primary_owner", "")).strip(),
                "owners": sorted(
                    [item for item in row.get("owners", []) if isinstance(item, dict)],
                    key=owner_key,
                ),
                "commitments": sorted(
                    [item for item in row.get("commitments", []) if isinstance(item, dict)],
                    key=commitment_key,
                ),
                "dependencies": sorted(
                    [item for item in row.get("dependencies", []) if isinstance(item, dict)],
                    key=dependency_key,
                ),
                "decision_gaps": sorted(
                    [item for item in row.get("decision_gaps", []) if isinstance(item, dict)],
                    key=gap_key,
                ),
                "timeline_signals": sorted(
                    [item for item in row.get("timeline_signals", []) if isinstance(item, dict)],
                    key=signal_key,
                ),
                "evidence": _normalize_evidence(row.get("evidence", [])),
            }
        )

    normalized_records = sorted(
        normalized_records,
        key=lambda x: (
            x["decision_id"].lower() or "~",
            x["statement"].lower(),
        ),
    )
    return {
        "critical": {
            "decision_records": normalized_records,
            "operational_summary": payload.get("operational_summary", {}),
        }
    }


def write_normalized_snapshot(meeting_id: str, artifact_name: str, normalized: dict) -> Path:
    root = (
        Path("data")
        / "processed"
        / meeting_id
        / app_config.REGRESSION_OUTPUT_DIR
        / app_config.REGRESSION_NORMALIZED_DIR
    )
    relative = Path(artifact_name)
    target = root / f"{relative}.normalized.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    return target

