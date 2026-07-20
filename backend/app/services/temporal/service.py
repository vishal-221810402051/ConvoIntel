"""Canonical temporal intelligence publication service."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from backend.app.config import Settings, get_settings
from backend.app.models.cleanup import (
    CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH,
    CleanedTranscript,
    CleanedTranscriptSegment,
)
from backend.app.models.intelligence import (
    INTELLIGENCE_JSON_RELATIVE_PATH,
    INTELLIGENCE_METADATA_RELATIVE_PATH,
    DecisionIntelligenceArtifact,
    DecisionIntelligenceResult,
    IntelligenceEvidenceReference,
    IntelligenceGap,
    IntelligenceUsage,
)
from backend.app.models.temporal import (
    TEMPORAL_JSON_RELATIVE_PATH,
    TEMPORAL_METADATA_RELATIVE_PATH,
    TEMPORAL_PROMPT_VERSION,
    TEMPORAL_REASONING_EFFORT,
    TEMPORAL_RESPONSE_SCHEMA_NAME,
    TemporalEvidenceReference,
    TemporalGap,
    TemporalInputMetadata,
    TemporalIntelligenceArtifact,
    TemporalIntelligenceReference,
    TemporalIntelligenceResult,
    TemporalItem,
    TemporalMetadata,
    TemporalOutputMetadata,
    TemporalProcessingMetadata,
    TemporalProviderMetadata,
    TemporalReference,
    empty_temporal_intelligence,
)
from backend.app.services.intelligence.errors import (
    DecisionIntelligenceError,
    IntelligenceInputNotFoundError,
    IntelligenceStateError,
)
from backend.app.services.intelligence.service import DecisionIntelligenceService
from backend.app.services.temporal.errors import (
    TemporalEvidenceError,
    TemporalInputIntegrityError,
    TemporalInputNotFoundError,
    TemporalInputTooLargeError,
    TemporalIntelligenceError,
    TemporalIntelligenceReferenceError,
    TemporalMetadataWriteError,
    TemporalNormalizationError,
    TemporalProviderResponseError,
    TemporalPublicationError,
    TemporalStateError,
)
from backend.app.services.temporal.normalization import (
    normalize_provider_item,
    normalize_temporal_reference,
)
from backend.app.services.temporal.openai_provider import OpenAITemporalProvider
from backend.app.services.temporal.provider import (
    ProviderTemporalItem,
    ProviderTemporalResponse,
    TemporalProvider,
    TemporalProviderRequest,
)

logger = logging.getLogger(__name__)

MEETING_ID_PATTERN = re.compile(
    r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$",
)
HASH_BUFFER_SIZE = 1024 * 1024
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TEMPORAL_TEXT_PATTERN = re.compile(
    r"\b("
    r"deadline|due|date|time|when|tomorrow|today|yesterday|friday|monday|"
    r"tuesday|wednesday|thursday|saturday|sunday|week|month|year|day|"
    r"remind|reminder|schedule|before|after|until|by"
    r")\b",
    re.IGNORECASE,
)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class _TemporalContext:
    meeting_id: str
    meeting_dir: Path
    phase6_result: DecisionIntelligenceResult
    cleaned_transcript: CleanedTranscript
    cleaned_json_path: Path
    cleaned_json_size_bytes: int
    cleaned_json_sha256: str
    intelligence_json_path: Path
    intelligence_json_size_bytes: int
    intelligence_json_sha256: str
    intelligence_metadata_path: Path
    intelligence_metadata_size_bytes: int
    intelligence_metadata_sha256: str


class _Phase6ProviderGuard:
    def analyze(self, request: Any) -> Any:
        raise TemporalStateError(
            "Phase 6 decision intelligence must already be complete before Phase 7."
        )


class TemporalIntelligenceService:
    """Publish trusted, evidence-grounded temporal intelligence."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: TemporalProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def extract_temporal_intelligence(
        self,
        meeting_id: str,
        *,
        reference_datetime: datetime | None = None,
        timezone_name: str | None = None,
    ) -> TemporalIntelligenceResult:
        """Extract and publish Phase 7 temporal intelligence for a meeting."""

        reference = normalize_temporal_reference(reference_datetime, timezone_name)
        context = self._load_context(meeting_id)

        temporal_path = context.meeting_dir / TEMPORAL_JSON_RELATIVE_PATH
        metadata_path = context.meeting_dir / TEMPORAL_METADATA_RELATIVE_PATH

        existing = self._reuse_existing_if_valid(
            context,
            temporal_path,
            metadata_path,
            reference,
        )
        if existing is not None:
            return existing

        staging_dir = context.meeting_dir / ".staging" / f"temporal_{uuid.uuid4().hex}"

        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_temporal_path = staging_dir / "temporal_intelligence.json"
            staged_metadata_path = staging_dir / "temporal.json"

            payload_json = self._serialize_temporal_payload(context, reference)
            input_character_count = len(payload_json)
            if input_character_count > self._settings.temporal_max_input_characters:
                raise TemporalInputTooLargeError(
                    "Temporal input exceeds the configured provider input limit."
                )

            provider_request_count = 0
            usage: IntelligenceUsage | None = None
            if not context.cleaned_transcript.segments:
                temporal = empty_temporal_intelligence(
                    meeting_id=context.meeting_id,
                    cleaned_sha256=context.cleaned_json_sha256,
                    intelligence_sha256=context.intelligence_json_sha256,
                    intelligence_metadata_sha256=context.intelligence_metadata_sha256,
                    temporal_reference=reference,
                )
            else:
                provider = self._provider or OpenAITemporalProvider(self._settings)
                request = TemporalProviderRequest(
                    meeting_id=context.meeting_id,
                    model=self._settings.temporal_model,
                    prompt_version=TEMPORAL_PROMPT_VERSION,
                    response_schema_name=TEMPORAL_RESPONSE_SCHEMA_NAME,
                    reasoning_effort=TEMPORAL_REASONING_EFFORT,
                    max_output_tokens=self._settings.temporal_max_output_tokens,
                    max_items=self._settings.temporal_max_items,
                    input_character_count=input_character_count,
                    temporal_payload_json=payload_json,
                )
                provider_result = provider.extract(request)
                provider_request_count = 1
                usage = provider_result.usage
                temporal = self._build_canonical_temporal(
                    context,
                    provider_result.temporal,
                    reference,
                )

            self._validate_canonical_temporal(context, temporal, reference)
            self._write_json_atomically(
                temporal,
                staged_temporal_path,
                TemporalPublicationError,
                "Temporal intelligence JSON could not be written.",
            )
            temporal_size, temporal_sha = self._inspect_file(
                staged_temporal_path,
                TemporalPublicationError,
            )
            metadata = self._build_metadata(
                context,
                temporal,
                temporal_size,
                temporal_sha,
                provider_request_count=provider_request_count,
                input_character_count=input_character_count,
                reference=reference,
                usage=usage,
            )
            self._write_json_atomically(
                metadata,
                staged_metadata_path,
                TemporalMetadataWriteError,
                "Temporal intelligence metadata could not be written.",
            )
            self._publish_artifacts(
                staged_temporal_path,
                temporal_path,
                staged_metadata_path,
                metadata_path,
            )

            return TemporalIntelligenceResult(
                meeting_id=context.meeting_id,
                meeting_dir=context.meeting_dir.resolve(strict=False),
                temporal_json_path=temporal_path.resolve(strict=False),
                temporal_metadata_path=metadata_path.resolve(strict=False),
                temporal_intelligence=temporal,
                metadata=metadata,
                reused_existing=False,
            )
        except TemporalIntelligenceError:
            raise
        except OSError as exc:
            raise TemporalPublicationError(
                "Temporal intelligence artifacts could not be staged or published."
            ) from exc
        finally:
            self._rollback_staging(staging_dir)

    def _load_context(self, meeting_id: str) -> _TemporalContext:
        if not MEETING_ID_PATTERN.fullmatch(meeting_id):
            raise TemporalInputIntegrityError("Meeting ID is invalid.")

        meetings_dir = self._settings.meetings_dir
        meeting_dir = (meetings_dir / meeting_id).resolve(strict=False)
        if not self._is_relative_to(meeting_dir, meetings_dir):
            raise TemporalInputNotFoundError("Meeting package path is invalid.")
        if not meeting_dir.exists() or not meeting_dir.is_dir():
            raise TemporalInputNotFoundError("Meeting package was not found.")

        intelligence_path = meeting_dir / INTELLIGENCE_JSON_RELATIVE_PATH
        intelligence_metadata_path = meeting_dir / INTELLIGENCE_METADATA_RELATIVE_PATH
        if intelligence_path.exists() != intelligence_metadata_path.exists():
            raise TemporalStateError(
                "Meeting package contains partial Phase 6 intelligence state."
            )
        if not intelligence_path.exists():
            raise TemporalInputNotFoundError(
                "Phase 6 decision intelligence is required before temporal extraction."
            )

        try:
            phase6_result = DecisionIntelligenceService(
                self._settings,
                provider=_Phase6ProviderGuard(),
            ).analyze_meeting(meeting_id)
        except TemporalStateError:
            raise
        except IntelligenceInputNotFoundError as exc:
            raise TemporalInputNotFoundError(
                "Phase 1-6 input chain is incomplete."
            ) from exc
        except IntelligenceStateError as exc:
            raise TemporalStateError("Phase 6 intelligence state is invalid.") from exc
        except DecisionIntelligenceError as exc:
            raise TemporalInputIntegrityError(
                "Phase 1-6 input chain failed integrity validation."
            ) from exc

        cleaned_json_path = meeting_dir / CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH
        cleaned = self._load_model(
            cleaned_json_path,
            CleanedTranscript,
            TemporalInputNotFoundError,
            "Cleaned transcript JSON was not found.",
            "Cleaned transcript JSON is invalid for temporal intelligence.",
        )
        if cleaned.meeting_id != meeting_id:
            raise TemporalInputIntegrityError("Cleaned transcript meeting ID mismatch.")
        cleaned_size, cleaned_sha = self._inspect_file(
            cleaned_json_path,
            TemporalInputIntegrityError,
        )
        intelligence_size, intelligence_sha = self._inspect_file(
            intelligence_path,
            TemporalInputIntegrityError,
        )
        metadata_size, metadata_sha = self._inspect_file(
            intelligence_metadata_path,
            TemporalInputIntegrityError,
        )
        if cleaned_sha != phase6_result.intelligence.source_cleaned_transcript_sha256:
            raise TemporalInputIntegrityError(
                "Cleaned transcript checksum does not match Phase 6 provenance."
            )
        if intelligence_sha != phase6_result.metadata.output.intelligence_sha256:
            raise TemporalInputIntegrityError(
                "Decision intelligence checksum does not match metadata."
            )

        return _TemporalContext(
            meeting_id=meeting_id,
            meeting_dir=meeting_dir,
            phase6_result=phase6_result,
            cleaned_transcript=cleaned,
            cleaned_json_path=cleaned_json_path,
            cleaned_json_size_bytes=cleaned_size,
            cleaned_json_sha256=cleaned_sha,
            intelligence_json_path=intelligence_path,
            intelligence_json_size_bytes=intelligence_size,
            intelligence_json_sha256=intelligence_sha,
            intelligence_metadata_path=intelligence_metadata_path,
            intelligence_metadata_size_bytes=metadata_size,
            intelligence_metadata_sha256=metadata_sha,
        )

    def _load_model(
        self,
        path: Path,
        model_type: Any,
        missing_error: type[TemporalIntelligenceError],
        missing_message: str,
        invalid_message: str,
    ) -> Any:
        if not path.exists():
            raise missing_error(missing_message)
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            if missing_error is TemporalStateError:
                raise TemporalStateError(invalid_message) from exc
            raise TemporalInputIntegrityError(invalid_message) from exc

    def _serialize_temporal_payload(
        self,
        context: _TemporalContext,
        reference: TemporalReference | None,
    ) -> str:
        payload = {
            "meeting_id": context.meeting_id,
            "temporal_reference": (
                None
                if reference is None
                else {
                    "reference_datetime_local": reference.model_dump(mode="json")[
                        "reference_datetime_local"
                    ],
                    "timezone_name": reference.timezone_name,
                    "source": reference.source,
                }
            ),
            "segments": [
                {
                    "segment_order": index,
                    "segment_id": segment.segment_id,
                    "speaker_label": segment.speaker_label,
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                    "cleaned_text": segment.cleaned_text,
                }
                for index, segment in enumerate(context.cleaned_transcript.segments)
            ],
            "intelligence_items": self._phase6_temporal_context(
                context.phase6_result.intelligence
            ),
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _phase6_temporal_context(
        self,
        intelligence: DecisionIntelligenceArtifact,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for decision in intelligence.decisions:
            items.append(
                {
                    "item_type": "decision",
                    "item_id": decision.decision_id,
                    "text": decision.statement,
                    "evidence_segment_ids": self._evidence_segment_ids(decision.evidence),
                }
            )
        for action in intelligence.action_items:
            items.append(
                {
                    "item_type": "action_item",
                    "item_id": action.action_id,
                    "text": action.description,
                    "deadline_status": action.deadline.status,
                    "deadline_text": action.deadline.text,
                    "evidence_segment_ids": self._evidence_segment_ids(action.evidence),
                }
            )
        for commitment in intelligence.commitments:
            items.append(
                {
                    "item_type": "commitment",
                    "item_id": commitment.commitment_id,
                    "text": commitment.statement,
                    "deadline_status": commitment.deadline.status,
                    "deadline_text": commitment.deadline.text,
                    "evidence_segment_ids": self._evidence_segment_ids(
                        commitment.evidence
                    ),
                }
            )
        for follow_up in intelligence.follow_ups:
            items.append(
                {
                    "item_type": "follow_up",
                    "item_id": follow_up.follow_up_id,
                    "text": follow_up.description,
                    "deadline_status": follow_up.deadline.status,
                    "deadline_text": follow_up.deadline.text,
                    "evidence_segment_ids": self._evidence_segment_ids(
                        follow_up.evidence
                    ),
                }
            )
        for missing in intelligence.missing_information:
            if TEMPORAL_TEXT_PATTERN.search(missing.description):
                items.append(
                    {
                        "item_type": "missing_information",
                        "item_id": missing.missing_info_id,
                        "text": missing.description,
                        "required_for": missing.required_for,
                        "evidence_segment_ids": self._evidence_segment_ids(
                            missing.evidence
                        ),
                    }
                )
        for gap in intelligence.gaps:
            if gap.kind in {"missing_deadline", "ambiguous_deadline"}:
                items.append(
                    {
                        "item_type": "gap",
                        "item_id": gap.gap_id,
                        "gap_kind": gap.kind,
                        "text": gap.description,
                        "related_item_type": gap.related_item_type,
                        "related_item_id": gap.related_item_id,
                        "evidence_segment_ids": self._evidence_segment_ids(gap.evidence),
                    }
                )
        return items

    def _build_canonical_temporal(
        self,
        context: _TemporalContext,
        provider: ProviderTemporalResponse,
        reference: TemporalReference | None,
    ) -> TemporalIntelligenceArtifact:
        if len(provider.items) > self._settings.temporal_max_items:
            raise TemporalProviderResponseError(
                "Temporal provider returned too many items."
            )
        self._reject_duplicate_provider_items(provider.items)

        items: list[TemporalItem] = []
        for index, provider_item in enumerate(provider.items, start=1):
            self._validate_provider_item(context, provider_item, reference)
            normalized = normalize_provider_item(provider_item, reference)
            evidence = self._resolve_evidence(context, provider_item.evidence_segment_ids)
            related = [
                TemporalIntelligenceReference(
                    item_type=related_item.item_type,
                    item_id=related_item.item_id,
                )
                for related_item in provider_item.related_intelligence_items
            ]
            items.append(
                TemporalItem(
                    temporal_id=self._id("temporal", index),
                    expression_text=provider_item.expression_text,
                    category=provider_item.category,
                    expression_type=provider_item.expression_type,
                    resolution_status=provider_item.resolution_status,
                    resolution_basis=provider_item.resolution_basis,
                    precision=provider_item.precision,
                    confidence=provider_item.confidence,
                    start_date=normalized.start_date,
                    start_time=normalized.start_time,
                    end_date=normalized.end_date,
                    end_time=normalized.end_time,
                    timezone_name=normalized.timezone_name,
                    utc_offset_minutes=normalized.utc_offset_minutes,
                    start_datetime_utc=normalized.start_datetime_utc,
                    end_datetime_utc=normalized.end_datetime_utc,
                    duration_value=normalized.duration_value,
                    duration_unit=normalized.duration_unit,
                    duration_seconds=normalized.duration_seconds,
                    recurrence_frequency=normalized.recurrence_frequency,
                    recurrence_interval=normalized.recurrence_interval,
                    recurrence_days=normalized.recurrence_days,
                    evidence=evidence,
                    related_intelligence_items=related,
                )
            )

        gaps = self._derive_gaps(context, items, reference)
        return TemporalIntelligenceArtifact(
            meeting_id=context.meeting_id,
            source_cleaned_transcript_sha256=context.cleaned_json_sha256,
            source_intelligence_sha256=context.intelligence_json_sha256,
            source_intelligence_metadata_sha256=context.intelligence_metadata_sha256,
            temporal_reference=reference,
            items=items,
            gaps=gaps,
        )

    def _validate_provider_item(
        self,
        context: _TemporalContext,
        item: ProviderTemporalItem,
        reference: TemporalReference | None,
    ) -> None:
        self._validate_text(item.expression_text, "expression_text")
        self._validate_evidence_ids(context, item.evidence_segment_ids)
        evidence_text = self._evidence_text(context, item.evidence_segment_ids)
        if item.expression_text not in evidence_text:
            raise TemporalEvidenceError(
                "Temporal expression text is absent from evidence."
            )
        self._validate_intelligence_references(
            context,
            item.related_intelligence_items,
            item.evidence_segment_ids,
        )
        try:
            normalize_provider_item(item, reference)
        except TemporalNormalizationError:
            raise

    def _validate_intelligence_references(
        self,
        context: _TemporalContext,
        references: Iterable[Any],
        evidence_segment_ids: list[str],
    ) -> None:
        known = self._intelligence_reference_index(context.phase6_result.intelligence)
        seen: set[tuple[str, str]] = set()
        temporal_evidence = set(evidence_segment_ids)
        for reference in references:
            key = (reference.item_type, reference.item_id)
            if key in seen:
                raise TemporalIntelligenceReferenceError(
                    "Duplicate intelligence reference."
                )
            seen.add(key)
            if key not in known:
                raise TemporalIntelligenceReferenceError(
                    "Temporal item references unknown Phase 6 intelligence."
                )
            if not temporal_evidence.intersection(known[key]):
                raise TemporalIntelligenceReferenceError(
                    "Temporal evidence does not overlap intelligence evidence."
                )

    def _intelligence_reference_index(
        self,
        intelligence: DecisionIntelligenceArtifact,
    ) -> dict[tuple[str, str], set[str]]:
        index: dict[tuple[str, str], set[str]] = {}
        for item in intelligence.decisions:
            index[("decision", item.decision_id)] = set(
                self._evidence_segment_ids(item.evidence)
            )
        for item in intelligence.action_items:
            index[("action_item", item.action_id)] = set(
                self._evidence_segment_ids(item.evidence)
            )
        for item in intelligence.commitments:
            index[("commitment", item.commitment_id)] = set(
                self._evidence_segment_ids(item.evidence)
            )
        for item in intelligence.follow_ups:
            index[("follow_up", item.follow_up_id)] = set(
                self._evidence_segment_ids(item.evidence)
            )
        for item in intelligence.missing_information:
            index[("missing_information", item.missing_info_id)] = set(
                self._evidence_segment_ids(item.evidence)
            )
        for item in intelligence.gaps:
            index[("gap", item.gap_id)] = set(self._evidence_segment_ids(item.evidence))
        return index

    def _derive_gaps(
        self,
        context: _TemporalContext,
        items: list[TemporalItem],
        reference: TemporalReference | None,
    ) -> list[TemporalGap]:
        gaps: list[TemporalGap] = []
        seen: set[tuple[Any, ...]] = set()

        def add_gap(
            *,
            kind: str,
            description: str,
            related_temporal_id: str | None,
            related_intelligence_item: TemporalIntelligenceReference | None,
            evidence: list[TemporalEvidenceReference],
        ) -> None:
            key = (
                kind,
                description,
                related_temporal_id,
                None
                if related_intelligence_item is None
                else (
                    related_intelligence_item.item_type,
                    related_intelligence_item.item_id,
                ),
                tuple(item.segment_id for item in evidence),
            )
            if key in seen:
                return
            seen.add(key)
            gaps.append(
                TemporalGap(
                    gap_id=self._id("gap", len(gaps) + 1),
                    kind=kind,
                    description=description,
                    related_temporal_id=related_temporal_id,
                    related_intelligence_item=related_intelligence_item,
                    evidence=evidence,
                )
            )

        for phase6_gap in context.phase6_result.intelligence.gaps:
            if phase6_gap.kind not in {"missing_deadline", "ambiguous_deadline"}:
                continue
            add_gap(
                kind=phase6_gap.kind,
                description=phase6_gap.description,
                related_temporal_id=None,
                related_intelligence_item=TemporalIntelligenceReference(
                    item_type=phase6_gap.related_item_type,
                    item_id=phase6_gap.related_item_id,
                ),
                evidence=self._copy_phase6_evidence(phase6_gap.evidence),
            )

        for item in items:
            if item.resolution_status == "unresolved":
                add_gap(
                    kind="unresolved_expression",
                    description=f"Temporal expression is unresolved: {item.expression_text}",
                    related_temporal_id=item.temporal_id,
                    related_intelligence_item=None,
                    evidence=item.evidence,
                )
            if (
                reference is None
                and item.expression_type in {"relative", "deictic"}
                and item.resolution_status == "unresolved"
            ):
                add_gap(
                    kind="missing_reference",
                    description=(
                        "Trusted meeting reference is required to resolve temporal "
                        f"expression: {item.expression_text}"
                    ),
                    related_temporal_id=item.temporal_id,
                    related_intelligence_item=None,
                    evidence=item.evidence,
                )

        for related_ref, conflict_items in self._conflicting_deadlines(items):
            evidence = self._merge_evidence(
                evidence
                for item in conflict_items
                for evidence in item.evidence
            )
            add_gap(
                kind="conflicting_temporal_information",
                description=(
                    "Conflicting resolved temporal values reference the same "
                    "Phase 6 intelligence item."
                ),
                related_temporal_id=None,
                related_intelligence_item=related_ref,
                evidence=evidence,
            )

        return gaps

    def _conflicting_deadlines(
        self,
        items: list[TemporalItem],
    ) -> list[tuple[TemporalIntelligenceReference, list[TemporalItem]]]:
        grouped: dict[tuple[str, str], list[TemporalItem]] = {}
        refs: dict[tuple[str, str], TemporalIntelligenceReference] = {}
        for item in items:
            if item.category != "deadline":
                continue
            if item.resolution_status not in {"resolved_exact", "resolved_relative"}:
                continue
            value = (item.start_date, item.start_time, item.end_date, item.end_time)
            if not any(value):
                continue
            for reference in item.related_intelligence_items:
                key = (reference.item_type, reference.item_id)
                refs[key] = reference
                grouped.setdefault(key, []).append(item)

        conflicts: list[tuple[TemporalIntelligenceReference, list[TemporalItem]]] = []
        for key, group in grouped.items():
            values = {
                (item.start_date, item.start_time, item.end_date, item.end_time)
                for item in group
            }
            if len(values) > 1:
                conflicts.append((refs[key], group))
        return conflicts

    def _build_metadata(
        self,
        context: _TemporalContext,
        temporal: TemporalIntelligenceArtifact,
        temporal_size: int,
        temporal_sha: str,
        *,
        provider_request_count: int,
        input_character_count: int,
        reference: TemporalReference | None,
        usage: IntelligenceUsage | None,
    ) -> TemporalMetadata:
        return TemporalMetadata(
            meeting_id=context.meeting_id,
            created_at_utc=self._utc_now(),
            provider=TemporalProviderMetadata(),
            input=TemporalInputMetadata(
                cleaned_json_size_bytes=context.cleaned_json_size_bytes,
                cleaned_json_sha256=context.cleaned_json_sha256,
                intelligence_size_bytes=context.intelligence_json_size_bytes,
                intelligence_sha256=context.intelligence_json_sha256,
                intelligence_metadata_size_bytes=(
                    context.intelligence_metadata_size_bytes
                ),
                intelligence_metadata_sha256=context.intelligence_metadata_sha256,
                segment_count=len(context.cleaned_transcript.segments),
                speaker_labels=context.cleaned_transcript.speaker_labels,
                temporal_reference=reference,
            ),
            output=TemporalOutputMetadata(
                temporal_size_bytes=temporal_size,
                temporal_sha256=temporal_sha,
                category_counts=temporal.category_counts(),
            ),
            processing=TemporalProcessingMetadata(
                provider_request_count=provider_request_count,
                input_character_count=input_character_count,
                max_input_characters=self._settings.temporal_max_input_characters,
                max_items=self._settings.temporal_max_items,
            ),
            usage=usage,
        )

    def _reuse_existing_if_valid(
        self,
        context: _TemporalContext,
        temporal_path: Path,
        metadata_path: Path,
        reference: TemporalReference | None,
    ) -> TemporalIntelligenceResult | None:
        temporal_exists = temporal_path.exists()
        metadata_exists = metadata_path.exists()
        if temporal_exists != metadata_exists:
            raise TemporalStateError(
                "Meeting package contains partial temporal intelligence state."
            )
        if not temporal_exists:
            return None

        temporal = self._load_model(
            temporal_path,
            TemporalIntelligenceArtifact,
            TemporalStateError,
            "Temporal intelligence JSON was not found.",
            "Temporal intelligence JSON is invalid.",
        )
        metadata = self._load_model(
            metadata_path,
            TemporalMetadata,
            TemporalStateError,
            "Temporal metadata was not found.",
            "Temporal metadata is invalid.",
        )
        self._validate_existing_metadata(
            context,
            temporal,
            metadata,
            temporal_path,
            reference,
        )
        return TemporalIntelligenceResult(
            meeting_id=context.meeting_id,
            meeting_dir=context.meeting_dir.resolve(strict=False),
            temporal_json_path=temporal_path.resolve(strict=False),
            temporal_metadata_path=metadata_path.resolve(strict=False),
            temporal_intelligence=temporal,
            metadata=metadata,
            reused_existing=True,
        )

    def _validate_existing_metadata(
        self,
        context: _TemporalContext,
        temporal: TemporalIntelligenceArtifact,
        metadata: TemporalMetadata,
        temporal_path: Path,
        reference: TemporalReference | None,
    ) -> None:
        if temporal.meeting_id != context.meeting_id or metadata.meeting_id != context.meeting_id:
            raise TemporalStateError("Temporal meeting ID does not match.")
        if self._reference_signature(temporal.temporal_reference) != self._reference_signature(reference):
            raise TemporalStateError("Temporal reference does not match request.")
        if self._reference_signature(metadata.input.temporal_reference) != self._reference_signature(reference):
            raise TemporalStateError("Temporal metadata reference does not match request.")
        if temporal.source_cleaned_transcript_sha256 != context.cleaned_json_sha256:
            raise TemporalStateError("Temporal source cleaned checksum does not match.")
        if temporal.source_intelligence_sha256 != context.intelligence_json_sha256:
            raise TemporalStateError("Temporal source intelligence checksum does not match.")
        if (
            temporal.source_intelligence_metadata_sha256
            != context.intelligence_metadata_sha256
        ):
            raise TemporalStateError(
                "Temporal source intelligence metadata checksum does not match."
            )
        if temporal.prompt_version != TEMPORAL_PROMPT_VERSION:
            raise TemporalStateError("Temporal prompt version does not match.")
        if metadata.provider.model != self._settings.temporal_model:
            raise TemporalStateError("Temporal provider model does not match.")
        if metadata.provider.prompt_version != TEMPORAL_PROMPT_VERSION:
            raise TemporalStateError("Temporal provider prompt does not match.")
        if metadata.provider.response_schema != TEMPORAL_RESPONSE_SCHEMA_NAME:
            raise TemporalStateError("Temporal response schema does not match.")
        if metadata.provider.store is not False:
            raise TemporalStateError("Temporal store flag does not match.")
        if metadata.provider.reasoning_effort != TEMPORAL_REASONING_EFFORT:
            raise TemporalStateError("Temporal reasoning effort does not match.")
        if metadata.input.cleaned_json_sha256 != context.cleaned_json_sha256:
            raise TemporalStateError("Temporal input cleaned checksum does not match.")
        if metadata.input.intelligence_sha256 != context.intelligence_json_sha256:
            raise TemporalStateError("Temporal input intelligence checksum does not match.")
        if (
            metadata.input.intelligence_metadata_sha256
            != context.intelligence_metadata_sha256
        ):
            raise TemporalStateError(
                "Temporal input intelligence metadata checksum does not match."
            )
        if metadata.input.segment_count != len(context.cleaned_transcript.segments):
            raise TemporalStateError("Temporal input segment count does not match.")
        if metadata.input.speaker_labels != context.cleaned_transcript.speaker_labels:
            raise TemporalStateError("Temporal input speaker labels do not match.")
        temporal_size, temporal_sha = self._inspect_file(temporal_path, TemporalStateError)
        if metadata.output.temporal_size_bytes != temporal_size:
            raise TemporalStateError("Temporal size does not match metadata.")
        if metadata.output.temporal_sha256 != temporal_sha:
            raise TemporalStateError("Temporal checksum does not match metadata.")
        if metadata.output.category_counts != temporal.category_counts():
            raise TemporalStateError("Temporal category counts do not match.")
        self._validate_canonical_temporal(context, temporal, reference)

    def _validate_canonical_temporal(
        self,
        context: _TemporalContext,
        temporal: TemporalIntelligenceArtifact,
        reference: TemporalReference | None,
    ) -> None:
        if temporal.meeting_id != context.meeting_id:
            raise TemporalStateError("Temporal meeting ID does not match.")
        if self._reference_signature(temporal.temporal_reference) != self._reference_signature(reference):
            raise TemporalStateError("Temporal reference does not match.")
        if temporal.source_cleaned_transcript_sha256 != context.cleaned_json_sha256:
            raise TemporalStateError("Temporal source cleaned checksum does not match.")
        if temporal.source_intelligence_sha256 != context.intelligence_json_sha256:
            raise TemporalStateError("Temporal source intelligence checksum does not match.")
        if (
            temporal.source_intelligence_metadata_sha256
            != context.intelligence_metadata_sha256
        ):
            raise TemporalStateError(
                "Temporal source intelligence metadata checksum does not match."
            )
        if [item.temporal_id for item in temporal.items] != [
            self._id("temporal", index)
            for index in range(1, len(temporal.items) + 1)
        ]:
            raise TemporalStateError("Temporal IDs are not canonical.")
        if [gap.gap_id for gap in temporal.gaps] != [
            self._id("gap", index) for index in range(1, len(temporal.gaps) + 1)
        ]:
            raise TemporalStateError("Temporal gap IDs are not canonical.")
        known = self._intelligence_reference_index(context.phase6_result.intelligence)
        for item in temporal.items:
            self._validate_evidence_references(context, item.evidence, TemporalStateError)
            for related in item.related_intelligence_items:
                key = (related.item_type, related.item_id)
                if key not in known:
                    raise TemporalStateError("Temporal intelligence link is invalid.")
                if not set(known[key]).intersection(
                    {evidence.segment_id for evidence in item.evidence}
                ):
                    raise TemporalStateError(
                        "Temporal intelligence link evidence does not overlap."
                    )
        for gap in temporal.gaps:
            if gap.evidence:
                self._validate_evidence_references(
                    context,
                    gap.evidence,
                    TemporalStateError,
                )
            if gap.related_temporal_id is not None and gap.related_temporal_id not in {
                item.temporal_id for item in temporal.items
            }:
                raise TemporalStateError("Temporal gap references unknown item.")
            if gap.related_intelligence_item is not None:
                key = (
                    gap.related_intelligence_item.item_type,
                    gap.related_intelligence_item.item_id,
                )
                if key not in known:
                    raise TemporalStateError("Temporal gap intelligence link is invalid.")

    def _validate_evidence_ids(
        self,
        context: _TemporalContext,
        segment_ids: list[str],
    ) -> None:
        if not segment_ids:
            raise TemporalEvidenceError("Evidence segment IDs are required.")
        id_to_index = self._segment_index(context)
        seen: set[str] = set()
        previous_index = -1
        for segment_id in segment_ids:
            if segment_id not in id_to_index:
                raise TemporalEvidenceError("Evidence segment ID does not exist.")
            if segment_id in seen:
                raise TemporalEvidenceError("Evidence segment IDs must be unique.")
            index = id_to_index[segment_id]
            if index <= previous_index:
                raise TemporalEvidenceError("Evidence segment IDs are out of order.")
            seen.add(segment_id)
            previous_index = index

    def _resolve_evidence(
        self,
        context: _TemporalContext,
        segment_ids: list[str],
    ) -> list[TemporalEvidenceReference]:
        self._validate_evidence_ids(context, segment_ids)
        segment_by_id = {
            segment.segment_id: segment for segment in context.cleaned_transcript.segments
        }
        return [
            TemporalEvidenceReference(
                segment_id=segment.segment_id,
                speaker_label=segment.speaker_label,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                cleaned_text_sha256=self._hash_text(segment.cleaned_text),
            )
            for segment_id in segment_ids
            for segment in [segment_by_id[segment_id]]
        ]

    def _validate_evidence_references(
        self,
        context: _TemporalContext,
        evidence: list[TemporalEvidenceReference],
        error_type: type[TemporalIntelligenceError],
    ) -> None:
        if not evidence:
            raise error_type("Canonical temporal evidence references are required.")
        segment_by_id = {
            segment.segment_id: segment for segment in context.cleaned_transcript.segments
        }
        self._validate_evidence_ids(context, [item.segment_id for item in evidence])
        for item in evidence:
            segment = segment_by_id[item.segment_id]
            if item.speaker_label != segment.speaker_label:
                raise error_type("Evidence speaker label does not match.")
            if item.start_seconds != segment.start_seconds:
                raise error_type("Evidence start timestamp does not match.")
            if item.end_seconds != segment.end_seconds:
                raise error_type("Evidence end timestamp does not match.")
            if item.cleaned_text_sha256 != self._hash_text(segment.cleaned_text):
                raise error_type("Evidence text checksum does not match.")

    def _copy_phase6_evidence(
        self,
        evidence: list[IntelligenceEvidenceReference],
    ) -> list[TemporalEvidenceReference]:
        return [
            TemporalEvidenceReference(
                segment_id=item.segment_id,
                speaker_label=item.speaker_label,
                start_seconds=item.start_seconds,
                end_seconds=item.end_seconds,
                cleaned_text_sha256=item.cleaned_text_sha256,
            )
            for item in evidence
        ]

    def _merge_evidence(
        self,
        evidence_items: Iterable[TemporalEvidenceReference],
    ) -> list[TemporalEvidenceReference]:
        by_id: dict[str, TemporalEvidenceReference] = {}
        ordered_ids: list[str] = []
        for evidence in evidence_items:
            if evidence.segment_id not in by_id:
                ordered_ids.append(evidence.segment_id)
                by_id[evidence.segment_id] = evidence
        return [by_id[segment_id] for segment_id in ordered_ids]

    def _evidence_segment_ids(
        self,
        evidence: list[IntelligenceEvidenceReference],
    ) -> list[str]:
        return [item.segment_id for item in evidence]

    def _evidence_text(
        self,
        context: _TemporalContext,
        segment_ids: list[str],
    ) -> str:
        self._validate_evidence_ids(context, segment_ids)
        segment_by_id = {
            segment.segment_id: segment for segment in context.cleaned_transcript.segments
        }
        return "\n".join(segment_by_id[segment_id].cleaned_text for segment_id in segment_ids)

    def _segment_index(self, context: _TemporalContext) -> dict[str, int]:
        return {
            segment.segment_id: index
            for index, segment in enumerate(context.cleaned_transcript.segments)
        }

    def _reject_duplicate_provider_items(
        self,
        items: list[ProviderTemporalItem],
    ) -> None:
        seen: set[str] = set()
        for item in items:
            payload = item.model_dump(mode="json")
            key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if key in seen:
                raise TemporalProviderResponseError(
                    "Temporal provider returned duplicate items."
                )
            seen.add(key)

    def _validate_text(self, value: str, field_name: str) -> None:
        if CONTROL_CHARACTER_PATTERN.search(value):
            raise TemporalProviderResponseError(
                f"{field_name} contains control characters."
            )
        if not value.strip():
            raise TemporalProviderResponseError(f"{field_name} must be nonempty.")

    def _write_json_atomically(
        self,
        model: TemporalIntelligenceArtifact | TemporalMetadata,
        path: Path,
        error_type: type[TemporalIntelligenceError],
        message: str,
    ) -> None:
        payload = model.model_dump_json(indent=2) + "\n"
        self._write_text_atomically(payload, path, error_type, message)

    def _write_text_atomically(
        self,
        payload: str,
        path: Path,
        error_type: type[TemporalIntelligenceError],
        message: str,
    ) -> None:
        temp_path = path.with_name(f".tmp_{uuid.uuid4().hex[:12]}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove temporary temporal artifact")
            raise error_type(message) from exc

    def _publish_artifacts(
        self,
        staged_temporal_path: Path,
        temporal_path: Path,
        staged_metadata_path: Path,
        metadata_path: Path,
    ) -> None:
        published: list[Path] = []
        try:
            temporal_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if temporal_path.exists() or metadata_path.exists():
                raise TemporalStateError(
                    "Meeting package contains an inconsistent temporal state."
                )
            os.replace(staged_temporal_path, temporal_path)
            published.append(temporal_path)
            if metadata_path.exists():
                raise TemporalStateError(
                    "Meeting package contains an inconsistent temporal state."
                )
            os.replace(staged_metadata_path, metadata_path)
            published.append(metadata_path)
        except TemporalStateError:
            self._remove_published_artifacts(published)
            raise
        except OSError as exc:
            self._remove_published_artifacts(published)
            raise TemporalPublicationError(
                "Temporal intelligence artifacts could not be published."
            ) from exc

    def _remove_published_artifacts(self, paths: list[Path]) -> None:
        for path in reversed(paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Rollback failed for published temporal artifact")

    def _rollback_staging(self, staging_dir: Path) -> None:
        try:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            staging_root = staging_dir.parent
            if staging_root.exists() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            logger.exception("Rollback failed for staged temporal artifacts")

    def _inspect_file(
        self,
        path: Path,
        error_type: type[TemporalIntelligenceError],
    ) -> tuple[int, str]:
        try:
            if not path.exists() or not path.is_file():
                if error_type is TemporalInputIntegrityError:
                    raise TemporalInputNotFoundError(
                        "Temporal input artifact is missing."
                    )
                raise error_type("Temporal artifact is missing.")
            size_bytes = path.stat().st_size
        except OSError as exc:
            raise error_type("Temporal artifact could not be inspected.") from exc

        if size_bytes <= 0:
            raise error_type("Temporal artifact is empty.")
        return size_bytes, self._hash_file(path, error_type)

    def _hash_file(
        self,
        path: Path,
        error_type: type[TemporalIntelligenceError],
    ) -> str:
        checksum = hashlib.sha256()
        try:
            with path.open("rb") as file:
                while chunk := file.read(HASH_BUFFER_SIZE):
                    checksum.update(chunk)
        except OSError as exc:
            raise error_type("Artifact could not be hashed.") from exc
        return checksum.hexdigest()

    def _hash_text(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _reference_signature(self, reference: TemporalReference | None) -> str:
        if reference is None:
            return "null"
        return json.dumps(reference.model_dump(mode="json"), sort_keys=True)

    def _id(self, prefix: str, index: int) -> str:
        return f"{prefix}_{index:03d}"

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _resolve_package_relative_path(
        self,
        package_root: Path,
        relative_path: str,
        error_type: type[TemporalIntelligenceError],
    ) -> Path:
        self._assert_safe_package_relative_path(relative_path, error_type)
        pure_path = PurePosixPath(relative_path)
        resolved = package_root.joinpath(*pure_path.parts).resolve(strict=False)
        if not self._is_relative_to(resolved, package_root):
            raise error_type("Package-relative path escapes the meeting package.")
        return resolved

    def _assert_safe_package_relative_path(
        self,
        relative_path: str,
        error_type: type[TemporalIntelligenceError],
    ) -> None:
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts or "\\" in relative_path:
            raise error_type("Package-relative path is unsafe.")

    def _is_relative_to(self, child: Path, parent: Path) -> bool:
        try:
            child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except ValueError:
            return False
        return True
