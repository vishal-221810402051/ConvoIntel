# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

from typing import Any

from app.models.regression import ArtifactComparisonResult, DriftItem


def _append(
    items: list[DriftItem],
    *,
    artifact: str,
    path: str,
    severity: str,
    expected: Any,
    actual: Any,
    message: str,
) -> None:
    items.append(
        DriftItem(
            artifact=artifact,
            field_path=path,
            severity=severity,
            expected=expected,
            actual=actual,
            message=message,
        )
    )


def _deep_compare(
    expected: Any,
    actual: Any,
    *,
    artifact: str,
    field_path: str,
    severity: str,
    drifts: list[DriftItem],
) -> None:
    if type(expected) is not type(actual):
        _append(
            drifts,
            artifact=artifact,
            path=field_path,
            severity=severity,
            expected=type(expected).__name__,
            actual=type(actual).__name__,
            message="Type mismatch",
        )
        return

    if isinstance(expected, dict):
        all_keys = sorted(set(expected.keys()) | set(actual.keys()))
        for key in all_keys:
            next_path = f"{field_path}.{key}" if field_path else str(key)
            if key not in expected:
                _append(
                    drifts,
                    artifact=artifact,
                    path=next_path,
                    severity=severity,
                    expected=None,
                    actual=actual.get(key),
                    message="Unexpected key in actual",
                )
                continue
            if key not in actual:
                _append(
                    drifts,
                    artifact=artifact,
                    path=next_path,
                    severity=severity,
                    expected=expected.get(key),
                    actual=None,
                    message="Missing key in actual",
                )
                continue
            _deep_compare(
                expected[key],
                actual[key],
                artifact=artifact,
                field_path=next_path,
                severity=severity,
                drifts=drifts,
            )
        return

    if isinstance(expected, list):
        if len(expected) != len(actual):
            _append(
                drifts,
                artifact=artifact,
                path=field_path,
                severity=severity,
                expected=len(expected),
                actual=len(actual),
                message="List length mismatch",
            )
        for idx, (left, right) in enumerate(zip(expected, actual)):
            _deep_compare(
                left,
                right,
                artifact=artifact,
                field_path=f"{field_path}[{idx}]",
                severity=severity,
                drifts=drifts,
            )
        return

    if expected != actual:
        _append(
            drifts,
            artifact=artifact,
            path=field_path,
            severity=severity,
            expected=expected,
            actual=actual,
            message="Value mismatch",
        )


def _build_result(
    artifact_name: str,
    drifts: list[DriftItem],
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = metrics or {}
    critical_count = sum(1 for item in drifts if item.severity == "critical")
    soft_count = sum(1 for item in drifts if item.severity == "soft")
    result = ArtifactComparisonResult(
        artifact_name=artifact_name,
        pass_status=critical_count == 0,
        critical_drift_count=critical_count,
        soft_drift_count=soft_count,
        metrics=metrics,
        drift_items=drifts,
    )
    return result.to_dict()


def compare_intelligence(expected: dict, actual: dict) -> dict:
    drifts: list[DriftItem] = []
    expected_critical = (expected if isinstance(expected, dict) else {}).get("critical", {})
    actual_critical = (actual if isinstance(actual, dict) else {}).get("critical", {})
    _deep_compare(
        expected_critical,
        actual_critical,
        artifact="intelligence",
        field_path="critical",
        severity="critical",
        drifts=drifts,
    )

    expected_summary = (
        ((expected if isinstance(expected, dict) else {}).get("soft_text", {})).get("summary", "")
    )
    actual_summary = (
        ((actual if isinstance(actual, dict) else {}).get("soft_text", {})).get("summary", "")
    )
    if expected_summary != actual_summary:
        _append(
            drifts,
            artifact="intelligence",
            path="soft_text.summary",
            severity="soft",
            expected=expected_summary,
            actual=actual_summary,
            message="Summary wording drift",
        )

    for section_name in [
        "decisions",
        "risks",
        "action_plan",
        "roadmap",
        "deadlines",
        "stakeholders",
        "timeline_mentions",
    ]:
        for idx, item in enumerate(actual_critical.get(section_name, [])):
            evidence = str(item.get("evidence", "")).strip() if isinstance(item, dict) else ""
            if not evidence:
                _append(
                    drifts,
                    artifact="intelligence",
                    path=f"critical.{section_name}[{idx}].evidence",
                    severity="critical",
                    expected="non-empty evidence",
                    actual=evidence,
                    message="Evidence missing",
                )

    metrics = {
        "expected_records_total": sum(
            len(expected_critical.get(name, []))
            for name in [
                "decisions",
                "risks",
                "action_plan",
                "roadmap",
                "deadlines",
                "stakeholders",
                "timeline_mentions",
            ]
            if isinstance(expected_critical.get(name, []), list)
        ),
        "actual_records_total": sum(
            len(actual_critical.get(name, []))
            for name in [
                "decisions",
                "risks",
                "action_plan",
                "roadmap",
                "deadlines",
                "stakeholders",
                "timeline_mentions",
            ]
            if isinstance(actual_critical.get(name, []), list)
        ),
    }
    return _build_result("intelligence", drifts, metrics)


def compare_executive(expected: dict, actual: dict) -> dict:
    drifts: list[DriftItem] = []
    expected_critical = (expected if isinstance(expected, dict) else {}).get("critical", {})
    actual_critical = (actual if isinstance(actual, dict) else {}).get("critical", {})

    _deep_compare(
        expected_critical,
        actual_critical,
        artifact="executive",
        field_path="critical",
        severity="critical",
        drifts=drifts,
    )

    expected_soft = (expected if isinstance(expected, dict) else {}).get("soft_text", {})
    actual_soft = (actual if isinstance(actual, dict) else {}).get("soft_text", {})
    _deep_compare(
        expected_soft,
        actual_soft,
        artifact="executive",
        field_path="soft_text",
        severity="soft",
        drifts=drifts,
    )

    metrics = {
        "expected_role_rows": len(expected_critical.get("role_clarity_assessment", []))
        if isinstance(expected_critical.get("role_clarity_assessment", []), list)
        else 0,
        "actual_role_rows": len(actual_critical.get("role_clarity_assessment", []))
        if isinstance(actual_critical.get("role_clarity_assessment", []), list)
        else 0,
    }
    return _build_result("executive", drifts, metrics)


def compare_decision(expected: dict, actual: dict) -> dict:
    drifts: list[DriftItem] = []
    expected_critical = (expected if isinstance(expected, dict) else {}).get("critical", {})
    actual_critical = (actual if isinstance(actual, dict) else {}).get("critical", {})
    expected_records = expected_critical.get("decision_records", [])
    actual_records = actual_critical.get("decision_records", [])

    if not isinstance(expected_records, list):
        expected_records = []
    if not isinstance(actual_records, list):
        actual_records = []

    expected_count = len(expected_records)
    actual_count = len(actual_records)
    count_delta = abs(expected_count - actual_count)
    if count_delta == 1:
        _append(
            drifts,
            artifact="decision",
            path="critical.decision_records",
            severity="soft",
            expected=expected_count,
            actual=actual_count,
            message="Decision record count drift by +/-1",
        )
    elif count_delta > 1:
        _append(
            drifts,
            artifact="decision",
            path="critical.decision_records",
            severity="critical",
            expected=expected_count,
            actual=actual_count,
            message="Decision record count drift greater than 1",
        )

    def key_of(row: dict[str, Any]) -> str:
        decision_id = str(row.get("decision_id", "")).strip()
        if decision_id:
            return f"id::{decision_id}"
        return f"statement::{str(row.get('statement', '')).strip().lower()}"

    expected_map = {key_of(row): row for row in expected_records if isinstance(row, dict)}
    actual_map = {key_of(row): row for row in actual_records if isinstance(row, dict)}
    all_keys = sorted(set(expected_map.keys()) | set(actual_map.keys()))

    for key in all_keys:
        path = f"critical.decision_records[{key}]"
        if key not in expected_map:
            sev = "soft" if count_delta == 1 else "critical"
            _append(
                drifts,
                artifact="decision",
                path=path,
                severity=sev,
                expected=None,
                actual=actual_map[key],
                message="Unexpected decision record",
            )
            continue
        if key not in actual_map:
            sev = "soft" if count_delta == 1 else "critical"
            _append(
                drifts,
                artifact="decision",
                path=path,
                severity=sev,
                expected=expected_map[key],
                actual=None,
                message="Missing decision record",
            )
            continue

        exp = expected_map[key]
        act = actual_map[key]

        for critical_field in [
            "decision_id",
            "statement",
            "state",
            "impact_level",
            "primary_owner",
            "owners",
            "dependencies",
            "decision_gaps",
            "timeline_signals",
            "evidence",
        ]:
            if exp.get(critical_field) != act.get(critical_field):
                _append(
                    drifts,
                    artifact="decision",
                    path=f"{path}.{critical_field}",
                    severity="critical",
                    expected=exp.get(critical_field),
                    actual=act.get(critical_field),
                    message=f"Critical decision field drift: {critical_field}",
                )

        if str(exp.get("confidence", "")).lower() != str(act.get("confidence", "")).lower():
            _append(
                drifts,
                artifact="decision",
                path=f"{path}.confidence",
                severity="soft",
                expected=exp.get("confidence"),
                actual=act.get("confidence"),
                message="Decision confidence drift",
            )

        exp_commitments = exp.get("commitments", []) if isinstance(exp.get("commitments", []), list) else []
        act_commitments = act.get("commitments", []) if isinstance(act.get("commitments", []), list) else []
        exp_commit_sig = sorted(
            [
                (
                    str(item.get("actor", "")).strip().lower(),
                    str(item.get("commitment_type", "")).strip().lower(),
                    str(item.get("status", "")).strip().lower(),
                )
                for item in exp_commitments
                if isinstance(item, dict)
            ]
        )
        act_commit_sig = sorted(
            [
                (
                    str(item.get("actor", "")).strip().lower(),
                    str(item.get("commitment_type", "")).strip().lower(),
                    str(item.get("status", "")).strip().lower(),
                )
                for item in act_commitments
                if isinstance(item, dict)
            ]
        )
        if exp_commit_sig != act_commit_sig:
            _append(
                drifts,
                artifact="decision",
                path=f"{path}.commitments.signature",
                severity="critical",
                expected=exp_commit_sig,
                actual=act_commit_sig,
                message="Commitment actor/type/status drift",
            )
        else:
            exp_text = sorted(
                [str(item.get("commitment", "")).strip() for item in exp_commitments if isinstance(item, dict)]
            )
            act_text = sorted(
                [str(item.get("commitment", "")).strip() for item in act_commitments if isinstance(item, dict)]
            )
            if exp_text != act_text:
                _append(
                    drifts,
                    artifact="decision",
                    path=f"{path}.commitments.text",
                    severity="soft",
                    expected=exp_text,
                    actual=act_text,
                    message="Commitment wording drift",
                )

        for dep_idx, dep in enumerate(act.get("dependencies", []) if isinstance(act.get("dependencies", []), list) else []):
            if not isinstance(dep, dict):
                continue
            if not str(dep.get("reason", "")).strip():
                _append(
                    drifts,
                    artifact="decision",
                    path=f"{path}.dependencies[{dep_idx}].reason",
                    severity="critical",
                    expected="non-empty reason",
                    actual=dep.get("reason"),
                    message="Dependency reason missing",
                )
        has_open_high = any(
            isinstance(dep, dict)
            and str(dep.get("status", "")).lower() == "open"
            and str(dep.get("blocking_level", "")).lower() == "high"
            for dep in (act.get("dependencies", []) if isinstance(act.get("dependencies", []), list) else [])
        )
        if has_open_high and str(act.get("state", "")).lower() != "blocked":
            _append(
                drifts,
                artifact="decision",
                path=f"{path}.state",
                severity="critical",
                expected="blocked",
                actual=act.get("state"),
                message="Blocked/open-high consistency broken",
            )

    if expected_critical.get("operational_summary") != actual_critical.get("operational_summary"):
        _append(
            drifts,
            artifact="decision",
            path="critical.operational_summary",
            severity="soft",
            expected=expected_critical.get("operational_summary"),
            actual=actual_critical.get("operational_summary"),
            message="Operational summary drift",
        )

    metrics = {
        "expected_decision_count": expected_count,
        "actual_decision_count": actual_count,
        "decision_count_delta": count_delta,
    }
    return _build_result("decision", drifts, metrics)

