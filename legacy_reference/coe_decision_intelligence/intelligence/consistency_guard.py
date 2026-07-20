# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import re
from typing import Any

from app.intelligence.evidence_engine import is_semantically_supportive


def _normalize(value: str) -> str:
    return str(value or "").strip().lower()


def _tokenize(text: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[A-Za-z0-9]+", str(text or "").lower())
        if len(tok) >= 3
    }


def _certainty_rank(value: str) -> int:
    table = {"uncertain": 0, "conditional": 1, "direct": 2}
    return table.get(_normalize(value), 0)


def _extract_intelligence_texts(intelligence: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not isinstance(intelligence, dict):
        return []
    rows: list[tuple[str, str]] = []
    for family in [
        "decisions",
        "risks",
        "action_plan",
        "roadmap",
        "deadlines",
        "stakeholders",
        "timeline_mentions",
    ]:
        values = intelligence.get(family, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            text = ""
            for key in ["text", "task", "step", "event", "name", "raw_time_reference"]:
                candidate = str(item.get(key, "")).strip()
                if candidate:
                    text = candidate
                    break
            if not text:
                continue
            certainty = str(item.get("certainty_class", "")).strip().upper() or "UNCERTAIN"
            rows.append((text, certainty))
    return rows


def _best_anchor_certainty(claim: str, anchors: list[tuple[str, str]]) -> str:
    claim_tokens = _tokenize(claim)
    best_overlap = 0
    best_certainty = "UNCERTAIN"
    for text, certainty in anchors:
        anchor_tokens = _tokenize(text)
        overlap = len(claim_tokens & anchor_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_certainty = certainty
    return best_certainty if best_overlap >= 2 else "UNCERTAIN"


def _is_anchored_to_intelligence(claim: str, anchors: list[tuple[str, str]]) -> bool:
    claim_tokens = _tokenize(claim)
    if not claim_tokens:
        return False
    for text, _ in anchors:
        if len(claim_tokens & _tokenize(text)) >= 2:
            return True
    return False


def validate_cross_artifact_consistency(
    executive: dict[str, Any],
    decision: dict[str, Any],
    intelligence: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    power = executive.get("power_structure", {}) if isinstance(executive, dict) else {}
    roles = executive.get("role_clarity_assessment", []) if isinstance(executive, dict) else []
    known_exec_actors = set()

    if isinstance(power, dict):
        for key in ["sponsor", "strategic_authority", "decision_makers", "advisors", "executors", "implementation_owner"]:
            values = power.get(key, [])
            if isinstance(values, list):
                known_exec_actors.update(_normalize(v) for v in values if str(v).strip())
    if isinstance(roles, list):
        for row in roles:
            if isinstance(row, dict):
                actor = _normalize(row.get("actor", ""))
                if actor:
                    known_exec_actors.add(actor)

    execution_risk = _normalize(
        (executive.get("execution_structure", {}) if isinstance(executive, dict) else {}).get("execution_risk_score", "")
    )
    intel_anchors = _extract_intelligence_texts(intelligence)

    records = decision.get("decision_records", []) if isinstance(decision, dict) else []
    if not isinstance(records, list):
        return ["decision.decision_records is not a list"]

    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            issues.append(f"decision_records[{idx}] is not an object")
            continue
        owner = _normalize(rec.get("primary_owner", ""))
        if owner and owner not in known_exec_actors:
            issues.append(
                f"decision_records[{idx}].primary_owner='{rec.get('primary_owner', '')}' not present in executive roles/power map"
            )
        owner_conf = float(rec.get("owner_confidence", 0.0) or 0.0)
        if owner and owner_conf < 0.75:
            issues.append(
                f"decision_records[{idx}] owner overconfidence: primary_owner set with owner_confidence={owner_conf:.2f}"
            )

        statement = str(rec.get("statement", "")).strip()
        if statement and intel_anchors and not _is_anchored_to_intelligence(statement, intel_anchors):
            issues.append(
                f"decision_records[{idx}] introduces unsupported concept outside canonical intelligence anchors"
            )

        deps = rec.get("dependencies", [])
        has_open_high_authority = False
        if isinstance(deps, list):
            for dep in deps:
                if not isinstance(dep, dict):
                    continue
                dep_conf = float(dep.get("evidence_confidence", 0.0) or 0.0)
                if dep_conf < 0.6:
                    issues.append(
                        f"decision_records[{idx}] weak dependency leakage: {dep.get('type', '')} evidence_confidence={dep_conf:.2f}"
                    )
                if (
                    _normalize(dep.get("type", "")) == "authority_dependency"
                    and _normalize(dep.get("status", "")) == "open"
                    and _normalize(dep.get("blocking_level", "")) == "high"
                ):
                    has_open_high_authority = True
                    break

        if has_open_high_authority and execution_risk not in {"high", "medium"}:
            issues.append(
                f"decision_records[{idx}] has open high authority blocker but executive execution risk is '{execution_risk or 'unknown'}'"
            )

        status = _normalize(rec.get("decision_status", rec.get("state", "")))
        evidence_text = " ".join(str(x) for x in rec.get("evidence", []) if isinstance(x, str)).lower()
        if "not a yes" in evidence_text and status == "confirmed":
            issues.append(
                f"decision_records[{idx}] certainty contradiction: evidence includes 'not a yes' but decision is confirmed"
            )

        certainty_class = str(rec.get("certainty_class", "")).strip().upper() or "UNCERTAIN"
        if intel_anchors:
            cap = _best_anchor_certainty(statement, intel_anchors)
            if _certainty_rank(certainty_class) > _certainty_rank(cap):
                issues.append(
                    f"decision_records[{idx}] certainty escalation: '{certainty_class}' exceeds lower-layer '{cap}'"
                )

        evidence_span = str(rec.get("evidence_span", "")).strip()
        claim = statement
        if evidence_span and not is_semantically_supportive(claim, evidence_span, claim_type="decision"):
            issues.append(
                f"decision_records[{idx}] semantic evidence mismatch between claim and evidence_span"
            )

    return issues
