# LEGACY REFERENCE ONLY

# Source: vishal-221810402051/CoE-Decision-Intelligence

# Not imported by the Convointel runtime.

# Port deliberately during the appropriate gated phase.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import app.config as app_config
from app.models.regression import (
    ArtifactComparisonResult,
    DriftItem,
    MeetingRegressionResult,
    RegressionSuiteReport,
    RepeatRunReport,
)
from app.services.decision import DecisionIntelligenceV2Service
from app.services.executive import ExecutiveIntelligenceService
from app.services.intelligence.contract import (
    get_canonical_intelligence_path,
    load_canonical_intelligence,
    validate_canonical_intelligence_contract,
)
from app.services.regression.comparator import (
    compare_decision,
    compare_executive,
    compare_intelligence,
)
from app.services.regression.normalizer import (
    normalize_decision_artifact,
    normalize_executive_artifact,
    normalize_intelligence_artifact,
    write_normalized_snapshot,
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


def _meeting_paths(meeting_id: str) -> dict[str, Path]:
    meeting_dir = Path("data") / "processed" / meeting_id
    return {
        "meeting_dir": meeting_dir,
        "transcript": meeting_dir / "transcript" / "transcript_clean.txt",
        "intelligence": get_canonical_intelligence_path(meeting_dir),
        "executive": meeting_dir / app_config.EXECUTIVE_OUTPUT_DIR / app_config.EXECUTIVE_OUTPUT_FILE,
        "decision": meeting_dir / app_config.DECISION_V2_OUTPUT_DIR / app_config.DECISION_V2_OUTPUT_FILE,
        "regression_dir": meeting_dir / app_config.REGRESSION_OUTPUT_DIR,
    }


def _extract_intelligence_evidence(data: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    for key in [
        "decisions",
        "risks",
        "action_plan",
        "roadmap",
        "deadlines",
        "stakeholders",
        "timeline_mentions",
    ]:
        for item in data.get(key, []):
            if isinstance(item, dict):
                text = str(item.get("evidence", "")).strip()
                if text:
                    evidence.append(text)
    return evidence


def _extract_executive_evidence(data: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    ex = data.get("execution_structure", {})
    if isinstance(ex, dict):
        values = ex.get("evidence", [])
        if isinstance(values, list):
            evidence.extend([str(v).strip() for v in values if isinstance(v, str) and str(v).strip()])
    for row in data.get("role_clarity_assessment", []):
        if isinstance(row, dict):
            value = str(row.get("evidence", "")).strip()
            if value:
                evidence.append(value)
    for row in data.get("negotiation_flags", []):
        if isinstance(row, dict):
            value = str(row.get("evidence", "")).strip()
            if value:
                evidence.append(value)
    for row in data.get("executive_warnings", []):
        if isinstance(row, dict):
            value = str(row.get("evidence", "")).strip()
            if value:
                evidence.append(value)
    return evidence


def _extract_decision_evidence(data: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    for row in data.get("decision_records", []):
        if not isinstance(row, dict):
            continue
        values = row.get("evidence", [])
        if isinstance(values, list):
            evidence.extend([str(v).strip() for v in values if isinstance(v, str) and str(v).strip()])
    return evidence


def _evidence_validity_ratio(evidence: list[str], transcript_text: str) -> float:
    if not evidence:
        return 1.0
    valid = sum(1 for ev in evidence if ev in transcript_text)
    return round(valid / len(evidence), 4)


def _contract_pass(canonical_intelligence: dict[str, Any]) -> bool:
    try:
        validate_canonical_intelligence_contract(canonical_intelligence)
    except Exception:
        return False
    return True


def _schema_pass(
    canonical_intelligence: dict[str, Any],
    executive: dict[str, Any],
    decision: dict[str, Any],
) -> bool:
    intelligence_keys = {
        "meeting_context",
        "summary",
        "decisions",
        "risks",
        "action_plan",
        "roadmap",
        "deadlines",
        "stakeholders",
        "timeline_mentions",
    }
    executive_keys = {
        "executive_summary",
        "strategic_objective",
        "power_structure",
        "execution_structure",
        "role_clarity_assessment",
        "business_model_clarity",
        "risk_posture",
        "negotiation_flags",
        "recommended_next_questions",
        "executive_warnings",
    }
    decision_keys = {"decision_records", "operational_summary"}
    return (
        isinstance(canonical_intelligence, dict)
        and isinstance(executive, dict)
        and isinstance(decision, dict)
        and intelligence_keys.issubset(set(canonical_intelligence.keys()))
        and executive_keys.issubset(set(executive.keys()))
        and decision_keys.issubset(set(decision.keys()))
    )


def _normalize_current_artifacts(meeting_id: str) -> dict[str, dict[str, Any]]:
    paths = _meeting_paths(meeting_id)
    canonical = load_canonical_intelligence(paths["meeting_dir"])
    executive = _load_json(paths["executive"])
    decision = _load_json(paths["decision"])
    return {
        "intelligence": normalize_intelligence_artifact(canonical),
        "executive": normalize_executive_artifact(executive),
        "decision": normalize_decision_artifact(decision),
    }


def _decision_quality_metrics(decision: dict[str, Any], transcript_text: str) -> dict[str, float]:
    records = decision.get("decision_records", [])
    if not isinstance(records, list):
        records = []

    total_records = len([row for row in records if isinstance(row, dict)])
    owned_records = 0
    total_signals = 0
    valid_signals = 0
    open_high_total = 0
    open_high_blocked = 0

    for row in records:
        if not isinstance(row, dict):
            continue
        if str(row.get("primary_owner", "")).strip():
            owned_records += 1

        signals = row.get("timeline_signals", [])
        if isinstance(signals, list):
            for sig in signals:
                if not isinstance(sig, dict):
                    continue
                raw = str(sig.get("raw_reference", "")).strip()
                if not raw:
                    continue
                total_signals += 1
                if raw in transcript_text:
                    valid_signals += 1

        deps = row.get("dependencies", [])
        has_open_high = any(
            isinstance(dep, dict)
            and str(dep.get("status", "")).lower() == "open"
            and str(dep.get("blocking_level", "")).lower() == "high"
            for dep in (deps if isinstance(deps, list) else [])
        )
        if has_open_high:
            open_high_total += 1
            if str(row.get("state", "")).lower() == "blocked":
                open_high_blocked += 1

    return {
        "owner_resolution_success_rate": round(owned_records / total_records, 4) if total_records else 1.0,
        "timeline_signal_precision": round(valid_signals / total_signals, 4) if total_signals else 1.0,
        "blocked_decision_consistency": round(open_high_blocked / open_high_total, 4) if open_high_total else 1.0,
    }


def run_repeat_run_check(meeting_id: str, runs: int | None = None) -> RepeatRunReport:
    repeat_runs = runs if isinstance(runs, int) and runs > 1 else app_config.REGRESSION_REPEAT_RUNS
    paths = _meeting_paths(meeting_id)
    transcript_text = paths["transcript"].read_text(encoding="utf-8")

    run_snapshots: list[dict[str, dict[str, Any]]] = []
    schema_passes = 0
    contract_passes = 0
    evidence_ratios: list[float] = []
    owner_resolution_rates: list[float] = []
    timeline_precision_rates: list[float] = []
    blocked_consistency_rates: list[float] = []
    executive_risk_scores: list[str] = []

    for run_index in range(1, repeat_runs + 1):
        ExecutiveIntelligenceService().run(meeting_id)
        DecisionIntelligenceV2Service().run(meeting_id)

        canonical = load_canonical_intelligence(paths["meeting_dir"])
        executive = _load_json(paths["executive"])
        decision = _load_json(paths["decision"])

        if _schema_pass(canonical, executive, decision):
            schema_passes += 1
        if _contract_pass(canonical):
            contract_passes += 1

        ev_all = (
            _extract_intelligence_evidence(canonical)
            + _extract_executive_evidence(executive)
            + _extract_decision_evidence(decision)
        )
        evidence_ratios.append(_evidence_validity_ratio(ev_all, transcript_text))
        decision_metrics_run = _decision_quality_metrics(decision, transcript_text)
        owner_resolution_rates.append(decision_metrics_run["owner_resolution_success_rate"])
        timeline_precision_rates.append(decision_metrics_run["timeline_signal_precision"])
        blocked_consistency_rates.append(decision_metrics_run["blocked_decision_consistency"])
        executive_risk_scores.append(
            str(
                (executive.get("execution_structure", {}) if isinstance(executive.get("execution_structure"), dict) else {}).get(
                    "execution_risk_score", ""
                )
            ).strip().lower()
        )

        normalized = {
            "intelligence": normalize_intelligence_artifact(canonical),
            "executive": normalize_executive_artifact(executive),
            "decision": normalize_decision_artifact(decision),
        }
        run_snapshots.append(normalized)
        run_tag = f"run_{run_index:02d}"
        write_normalized_snapshot(meeting_id, f"{run_tag}/intelligence", normalized["intelligence"])
        write_normalized_snapshot(meeting_id, f"{run_tag}/executive", normalized["executive"])
        write_normalized_snapshot(meeting_id, f"{run_tag}/decision", normalized["decision"])

    baseline = run_snapshots[0]
    comparisons: dict[str, list[dict[str, Any]]] = {"intelligence": [], "executive": [], "decision": []}
    for idx in range(1, len(run_snapshots)):
        current = run_snapshots[idx]
        comparisons["intelligence"].append(compare_intelligence(baseline["intelligence"], current["intelligence"]))
        comparisons["executive"].append(compare_executive(baseline["executive"], current["executive"]))
        comparisons["decision"].append(compare_decision(baseline["decision"], current["decision"]))

    artifact_results: list[ArtifactComparisonResult] = []
    for artifact_name in ["intelligence", "executive", "decision"]:
        critical_total = 0
        soft_total = 0
        merged_items: list[dict[str, Any]] = []
        decision_count_stable_pairs = 0
        decision_pairs = 0
        owner_stable_pairs = 0
        owner_pairs = 0

        for result in comparisons[artifact_name]:
            critical_total += int(result.get("critical_drift_count", 0))
            soft_total += int(result.get("soft_drift_count", 0))
            merged_items.extend(result.get("drift_items", []))
            if artifact_name == "decision":
                metrics = result.get("metrics", {})
                if isinstance(metrics, dict):
                    decision_pairs += 1
                    if int(metrics.get("decision_count_delta", 0)) == 0:
                        decision_count_stable_pairs += 1
                for item in result.get("drift_items", []):
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("field_path", "")).endswith(".primary_owner"):
                        owner_pairs += 1
                        if item.get("severity") != "critical":
                            owner_stable_pairs += 1

        metrics: dict[str, Any] = {"comparisons": len(comparisons[artifact_name])}
        if artifact_name == "decision":
            metrics["decision_count_stability_rate"] = (
                round(decision_count_stable_pairs / decision_pairs, 4) if decision_pairs else 1.0
            )
            metrics["owner_resolution_stability_rate"] = (
                round(owner_stable_pairs / owner_pairs, 4) if owner_pairs else 1.0
            )

        artifact_results.append(
            ArtifactComparisonResult(
                artifact_name=artifact_name,
                pass_status=critical_total == 0,
                critical_drift_count=critical_total,
                soft_drift_count=soft_total,
                metrics=metrics,
                drift_items=[
                    DriftItem(
                        artifact=str(item.get("artifact", artifact_name)),
                        field_path=str(item.get("field_path", "")),
                        severity=str(item.get("severity", "soft")),
                        expected=item.get("expected"),
                        actual=item.get("actual"),
                        message=str(item.get("message", "")),
                    )
                    for item in merged_items
                    if isinstance(item, dict)
                ],
            )
        )

    total_critical = sum(item.critical_drift_count for item in artifact_results)
    total_soft = sum(item.soft_drift_count for item in artifact_results)
    decision_metrics = next(
        (item.metrics for item in artifact_results if item.artifact_name == "decision"),
        {},
    )
    execution_risk_consistency = (
        round(max(executive_risk_scores.count(s) for s in set(executive_risk_scores)) / len(executive_risk_scores), 4)
        if executive_risk_scores
        else 1.0
    )
    comparisons_total = sum(int(item.metrics.get("comparisons", 0)) for item in artifact_results)
    output_drift_rate = round((total_critical + total_soft) / comparisons_total, 4) if comparisons_total else 0.0
    pass_status = (
        total_critical == 0
        and schema_passes == repeat_runs
        and contract_passes == repeat_runs
        and all(ratio == 1.0 for ratio in evidence_ratios)
    )
    summary_metrics = {
        "schema_pass_rate": round(schema_passes / repeat_runs, 4),
        "contract_pass_rate": round(contract_passes / repeat_runs, 4),
        "evidence_validity_rate": round(sum(evidence_ratios) / len(evidence_ratios), 4) if evidence_ratios else 0.0,
        "critical_drift_total": total_critical,
        "decision_count_stability_rate": float(decision_metrics.get("decision_count_stability_rate", 1.0)),
        "owner_resolution_stability_rate": float(decision_metrics.get("owner_resolution_stability_rate", 1.0)),
        "owner_resolution_success_rate": round(sum(owner_resolution_rates) / len(owner_resolution_rates), 4)
        if owner_resolution_rates
        else 1.0,
        "timeline_signal_precision": round(sum(timeline_precision_rates) / len(timeline_precision_rates), 4)
        if timeline_precision_rates
        else 1.0,
        "blocked_decision_consistency": round(sum(blocked_consistency_rates) / len(blocked_consistency_rates), 4)
        if blocked_consistency_rates
        else 1.0,
        "execution_risk_consistency": execution_risk_consistency,
        "output_drift_rate": output_drift_rate,
    }

    report = RepeatRunReport(
        meeting_id=meeting_id,
        runs=repeat_runs,
        pass_status=pass_status,
        artifact_results=artifact_results,
        summary_metrics=summary_metrics,
    )

    regression_dir = paths["regression_dir"]
    regression_dir.mkdir(parents=True, exist_ok=True)
    report_path = regression_dir / "repeat_run_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def run_regression_suite() -> RegressionSuiteReport:
    manifest_path = Path(app_config.BENCHMARKS_MANIFEST_PATH)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Benchmarks manifest not found: {manifest_path}")
    manifest = _load_json(manifest_path)
    entries = manifest.get("meetings", [])
    if not isinstance(entries, list):
        raise ValueError("benchmarks/manifest.json must contain a 'meetings' array")

    meeting_results: list[MeetingRegressionResult] = []
    total = 0
    passed = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not bool(entry.get("enabled", False)):
            continue
        total += 1
        meeting_id = str(entry.get("meeting_id", "")).strip()
        category = str(entry.get("category", "")).strip()
        difficulty = str(entry.get("difficulty", "")).strip()
        notes: list[str] = []

        ExecutiveIntelligenceService().run(meeting_id)
        DecisionIntelligenceV2Service().run(meeting_id)

        current = _normalize_current_artifacts(meeting_id)
        write_normalized_snapshot(meeting_id, "suite_current/intelligence", current["intelligence"])
        write_normalized_snapshot(meeting_id, "suite_current/executive", current["executive"])
        write_normalized_snapshot(meeting_id, "suite_current/decision", current["decision"])

        golden_dir = Path(app_config.BENCHMARKS_GOLDEN_DIR) / meeting_id
        artifact_results: list[ArtifactComparisonResult] = []
        all_pass = True

        for name, comparator in [
            ("intelligence", compare_intelligence),
            ("executive", compare_executive),
            ("decision", compare_decision),
        ]:
            golden_path = golden_dir / f"{name}.normalized.json"
            if not golden_path.exists():
                all_pass = False
                notes.append(f"Missing golden snapshot: {golden_path}")
                artifact_results.append(
                    ArtifactComparisonResult(
                        artifact_name=name,
                        pass_status=False,
                        critical_drift_count=1,
                        soft_drift_count=0,
                        metrics={"missing_golden": True},
                        drift_items=[
                            DriftItem(
                                artifact=name,
                                field_path="golden_snapshot",
                                severity="critical",
                                expected="present",
                                actual="missing",
                                message=f"Missing golden snapshot: {golden_path}",
                            )
                        ],
                    )
                )
                continue
            expected = _load_json(golden_path)
            compared = comparator(expected, current[name])
            artifact_results.append(
                ArtifactComparisonResult(
                    artifact_name=name,
                    pass_status=bool(compared.get("pass_status", False)),
                    critical_drift_count=int(compared.get("critical_drift_count", 0)),
                    soft_drift_count=int(compared.get("soft_drift_count", 0)),
                    metrics=dict(compared.get("metrics", {})),
                    drift_items=[
                        DriftItem(
                            artifact=str(item.get("artifact", name)),
                            field_path=str(item.get("field_path", "")),
                            severity=str(item.get("severity", "soft")),
                            expected=item.get("expected"),
                            actual=item.get("actual"),
                            message=str(item.get("message", "")),
                        )
                        for item in compared.get("drift_items", [])
                        if isinstance(item, dict)
                    ],
                )
            )
            if not artifact_results[-1].pass_status:
                all_pass = False

        if all_pass:
            passed += 1

        meeting_results.append(
            MeetingRegressionResult(
                meeting_id=meeting_id,
                category=category,
                difficulty=difficulty,
                pass_status=all_pass,
                artifact_results=artifact_results,
                summary_metrics={
                    "critical_drift_total": sum(item.critical_drift_count for item in artifact_results),
                    "soft_drift_total": sum(item.soft_drift_count for item in artifact_results),
                },
                notes=notes,
            )
        )

    suite = RegressionSuiteReport(
        total_meetings=total,
        passed_meetings=passed,
        failed_meetings=max(total - passed, 0),
        summary_metrics={
            "pass_rate": round(passed / total, 4) if total else 0.0,
        },
        meeting_results=meeting_results,
    )

    report_dir = Path(app_config.BENCHMARKS_REPORTS_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "latest_regression_report.json"
    report_path.write_text(json.dumps(suite.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return suite
