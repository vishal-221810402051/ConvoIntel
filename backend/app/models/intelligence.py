"""Typed models for canonical meeting decision intelligence artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.config import INTELLIGENCE_MODEL
from backend.app.models.cleanup import (
    CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH,
    CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH,
    CLEANUP_METADATA_RELATIVE_PATH,
)

INTELLIGENCE_PROVIDER_NAME = "openai"
INTELLIGENCE_ENDPOINT = "responses"
INTELLIGENCE_PROMPT_VERSION = "convointel-general-intelligence-v1"
INTELLIGENCE_RESPONSE_SCHEMA_NAME = "convointel_general_intelligence_v1"
INTELLIGENCE_REASONING_EFFORT = "low"
INTELLIGENCE_JSON_RELATIVE_PATH = "intelligence/decision_intelligence.json"
INTELLIGENCE_METADATA_RELATIVE_PATH = "metadata/intelligence.json"
INTELLIGENCE_STATUS = "intelligence_completed"
INTELLIGENCE_OUTPUT_LANGUAGE = "en"

DecisionStatus = Literal["confirmed", "provisional", "deferred", "rejected", "unclear"]
WorkStatus = Literal["open", "completed", "cancelled", "unclear"]
StakeholderStance = Literal["supportive", "opposed", "conditional", "neutral", "unclear"]
InferenceBasis = Literal["explicit", "strong_inference", "weak_inference"]
Priority = Literal["critical", "high", "medium", "low", "unspecified"]
PriorityBasis = Literal["explicit", "inferred", "unspecified"]
Confidence = Literal["high", "medium", "low"]
ActorKind = Literal["speaker_label", "named_person", "role", "team", "organization", "unknown"]
DeadlineStatus = Literal["explicit", "ambiguous", "missing", "not_applicable"]
GapKind = Literal["missing_owner", "missing_deadline", "ambiguous_deadline", "missing_information"]
RelatedItemType = Literal["action_item", "commitment", "follow_up", "missing_information"]

NonEmptyText = Annotated[str, Field(min_length=1)]


class IntelligenceActor(BaseModel):
    """Actor reference validated against transcript evidence."""

    kind: ActorKind
    value: str | None

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @model_validator(mode="after")
    def validate_value_relationship(self) -> "IntelligenceActor":
        if self.kind == "unknown":
            if self.value is not None:
                raise ValueError("unknown actors must use null value")
        elif self.value is None:
            raise ValueError("non-unknown actors require a value")
        return self


class IntelligenceDeadline(BaseModel):
    """Deadline reference that preserves transcript wording."""

    status: DeadlineStatus
    text: str | None

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @model_validator(mode="after")
    def validate_text_relationship(self) -> "IntelligenceDeadline":
        if self.status in {"explicit", "ambiguous"}:
            if self.text is None:
                raise ValueError("explicit and ambiguous deadlines require text")
        elif self.text is not None:
            raise ValueError("missing and not-applicable deadlines require null text")
        return self


class IntelligenceEvidenceReference(BaseModel):
    """Trusted local evidence reference resolved from the cleaned transcript."""

    segment_id: str = Field(min_length=1)
    speaker_label: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    cleaned_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "IntelligenceEvidenceReference":
        if self.end_seconds < self.start_seconds:
            raise ValueError("evidence end must not be before start")
        return self


class KeyOutcome(BaseModel):
    outcome_id: str = Field(pattern=r"^outcome_\d{3}$")
    statement: NonEmptyText
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class ExecutiveSummary(BaseModel):
    overview: str
    evidence: list[IntelligenceEvidenceReference]
    key_outcomes: list[KeyOutcome]


class DiscussionArea(BaseModel):
    discussion_id: str = Field(pattern=r"^discussion_\d{3}$")
    title: NonEmptyText
    summary: NonEmptyText
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class Decision(BaseModel):
    decision_id: str = Field(pattern=r"^decision_\d{3}$")
    statement: NonEmptyText
    status: DecisionStatus
    rationale: str | None
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class ActionItem(BaseModel):
    action_id: str = Field(pattern=r"^action_\d{3}$")
    description: NonEmptyText
    owner: IntelligenceActor
    deadline: IntelligenceDeadline
    priority: Priority
    priority_basis: PriorityBasis
    status: WorkStatus
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class Commitment(BaseModel):
    commitment_id: str = Field(pattern=r"^commitment_\d{3}$")
    statement: NonEmptyText
    actor: IntelligenceActor
    deadline: IntelligenceDeadline
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class FollowUp(BaseModel):
    follow_up_id: str = Field(pattern=r"^follow_up_\d{3}$")
    description: NonEmptyText
    owner: IntelligenceActor
    deadline: IntelligenceDeadline
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class StakeholderPosition(BaseModel):
    stakeholder_id: str = Field(pattern=r"^stakeholder_\d{3}$")
    actor: IntelligenceActor
    position: NonEmptyText
    stance: StakeholderStance
    concerns: list[NonEmptyText]
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class Risk(BaseModel):
    risk_id: str = Field(pattern=r"^risk_\d{3}$")
    description: NonEmptyText
    severity: Priority
    likelihood: Priority
    basis: InferenceBasis
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class Blocker(BaseModel):
    blocker_id: str = Field(pattern=r"^blocker_\d{3}$")
    description: NonEmptyText
    responsible_actor: IntelligenceActor
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class Dependency(BaseModel):
    dependency_id: str = Field(pattern=r"^dependency_\d{3}$")
    description: NonEmptyText
    dependency_on: IntelligenceActor
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class Opportunity(BaseModel):
    opportunity_id: str = Field(pattern=r"^opportunity_\d{3}$")
    description: NonEmptyText
    basis: InferenceBasis
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class UnresolvedQuestion(BaseModel):
    question_id: str = Field(pattern=r"^question_\d{3}$")
    question: NonEmptyText
    asked_by: IntelligenceActor
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class MissingInformation(BaseModel):
    missing_info_id: str = Field(pattern=r"^missing_info_\d{3}$")
    description: NonEmptyText
    required_for: str | None
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class StrategicInsight(BaseModel):
    insight_id: str = Field(pattern=r"^insight_\d{3}$")
    insight: NonEmptyText
    confidence: Confidence
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class Recommendation(BaseModel):
    recommendation_id: str = Field(pattern=r"^recommendation_\d{3}$")
    recommendation: NonEmptyText
    priority: Priority
    rationale: NonEmptyText
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class IntelligenceGap(BaseModel):
    gap_id: str = Field(pattern=r"^gap_\d{3}$")
    kind: GapKind
    description: NonEmptyText
    related_item_type: RelatedItemType
    related_item_id: str = Field(min_length=1)
    evidence: list[IntelligenceEvidenceReference] = Field(min_length=1)


class DecisionIntelligenceArtifact(BaseModel):
    """Canonical persisted decision-intelligence artifact."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    source_cleaned_transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_cleanup_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: Literal["convointel-general-intelligence-v1"] = (
        INTELLIGENCE_PROMPT_VERSION
    )
    output_language: Literal["en"] = INTELLIGENCE_OUTPUT_LANGUAGE
    executive_summary: ExecutiveSummary
    discussion_areas: list[DiscussionArea]
    decisions: list[Decision]
    action_items: list[ActionItem]
    commitments: list[Commitment]
    follow_ups: list[FollowUp]
    stakeholders: list[StakeholderPosition]
    risks: list[Risk]
    blockers: list[Blocker]
    dependencies: list[Dependency]
    opportunities: list[Opportunity]
    unresolved_questions: list[UnresolvedQuestion]
    missing_information: list[MissingInformation]
    strategic_insights: list[StrategicInsight]
    recommendations: list[Recommendation]
    gaps: list[IntelligenceGap]

    def category_counts(self) -> "IntelligenceCategoryCounts":
        return IntelligenceCategoryCounts(
            discussion_areas=len(self.discussion_areas),
            decisions=len(self.decisions),
            action_items=len(self.action_items),
            commitments=len(self.commitments),
            follow_ups=len(self.follow_ups),
            stakeholders=len(self.stakeholders),
            risks=len(self.risks),
            blockers=len(self.blockers),
            dependencies=len(self.dependencies),
            opportunities=len(self.opportunities),
            unresolved_questions=len(self.unresolved_questions),
            missing_information=len(self.missing_information),
            strategic_insights=len(self.strategic_insights),
            recommendations=len(self.recommendations),
            gaps=len(self.gaps),
        )


class IntelligenceProviderMetadata(BaseModel):
    name: Literal["openai"] = INTELLIGENCE_PROVIDER_NAME
    endpoint: Literal["responses"] = INTELLIGENCE_ENDPOINT
    model: Literal["gpt-5-mini-2025-08-07"] = INTELLIGENCE_MODEL
    prompt_version: Literal["convointel-general-intelligence-v1"] = (
        INTELLIGENCE_PROMPT_VERSION
    )
    response_format: Literal["json_schema"] = "json_schema"
    response_schema: Literal["convointel_general_intelligence_v1"] = (
        INTELLIGENCE_RESPONSE_SCHEMA_NAME
    )
    strict_schema: Literal[True] = True
    store: Literal[False] = False
    reasoning_effort: Literal["low"] = INTELLIGENCE_REASONING_EFFORT


class IntelligenceInputMetadata(BaseModel):
    cleaned_json_relative_path: Literal["transcript/cleaned.json"] = (
        CLEANED_TRANSCRIPT_JSON_RELATIVE_PATH
    )
    cleaned_json_size_bytes: int = Field(gt=0)
    cleaned_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cleaned_text_relative_path: Literal["transcript/cleaned.txt"] = (
        CLEANED_TRANSCRIPT_TEXT_RELATIVE_PATH
    )
    cleaned_text_size_bytes: int = Field(gt=0)
    cleaned_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cleanup_metadata_relative_path: Literal["metadata/cleanup.json"] = (
        CLEANUP_METADATA_RELATIVE_PATH
    )
    cleanup_metadata_size_bytes: int = Field(gt=0)
    cleanup_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_count: int = Field(ge=0)
    speaker_labels: list[str]

    @field_validator(
        "cleaned_json_relative_path",
        "cleaned_text_relative_path",
        "cleanup_metadata_relative_path",
    )
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class IntelligenceCategoryCounts(BaseModel):
    discussion_areas: int = Field(ge=0)
    decisions: int = Field(ge=0)
    action_items: int = Field(ge=0)
    commitments: int = Field(ge=0)
    follow_ups: int = Field(ge=0)
    stakeholders: int = Field(ge=0)
    risks: int = Field(ge=0)
    blockers: int = Field(ge=0)
    dependencies: int = Field(ge=0)
    opportunities: int = Field(ge=0)
    unresolved_questions: int = Field(ge=0)
    missing_information: int = Field(ge=0)
    strategic_insights: int = Field(ge=0)
    recommendations: int = Field(ge=0)
    gaps: int = Field(ge=0)


class IntelligenceOutputMetadata(BaseModel):
    intelligence_relative_path: Literal["intelligence/decision_intelligence.json"] = (
        INTELLIGENCE_JSON_RELATIVE_PATH
    )
    intelligence_size_bytes: int = Field(gt=0)
    intelligence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    category_counts: IntelligenceCategoryCounts

    @field_validator("intelligence_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        _validate_package_relative_path(value)
        return value


class IntelligenceProcessingMetadata(BaseModel):
    provider_request_count: int = Field(ge=0)
    input_character_count: int = Field(ge=0)
    max_input_characters: int = Field(ge=1)
    max_items_per_category: int = Field(ge=1, le=500)
    evidence_validation_passed: Literal[True] = True
    actor_validation_passed: Literal[True] = True
    deadline_validation_passed: Literal[True] = True


class IntelligenceUsage(BaseModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class DecisionIntelligenceMetadata(BaseModel):
    """Persisted `metadata/intelligence.json` contract."""

    schema_version: Literal["1.0"] = "1.0"
    meeting_id: str = Field(pattern=r"^mtg_\d{8}T\d{6}\d{6}Z_[0-9a-f]{8,32}$")
    created_at_utc: datetime
    status: Literal["intelligence_completed"] = INTELLIGENCE_STATUS
    provider: IntelligenceProviderMetadata
    input: IntelligenceInputMetadata
    output: IntelligenceOutputMetadata
    processing: IntelligenceProcessingMetadata
    usage: IntelligenceUsage | None = None

    @field_validator("created_at_utc")
    @classmethod
    def validate_created_at_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at_utc must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_serializer("created_at_utc")
    def serialize_created_at_utc(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds",
        ).replace("+00:00", "Z")


class DecisionIntelligenceResult(BaseModel):
    """Runtime result for decision-intelligence service calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meeting_id: str
    meeting_dir: Path
    intelligence_json_path: Path
    intelligence_metadata_path: Path
    intelligence: DecisionIntelligenceArtifact
    metadata: DecisionIntelligenceMetadata
    reused_existing: bool


def empty_decision_intelligence(
    *,
    meeting_id: str,
    cleaned_sha256: str,
    cleanup_metadata_sha256: str,
) -> DecisionIntelligenceArtifact:
    """Build a valid empty intelligence artifact for empty transcripts."""

    return DecisionIntelligenceArtifact(
        meeting_id=meeting_id,
        source_cleaned_transcript_sha256=cleaned_sha256,
        source_cleanup_metadata_sha256=cleanup_metadata_sha256,
        executive_summary=ExecutiveSummary(
            overview="",
            evidence=[],
            key_outcomes=[],
        ),
        discussion_areas=[],
        decisions=[],
        action_items=[],
        commitments=[],
        follow_ups=[],
        stakeholders=[],
        risks=[],
        blockers=[],
        dependencies=[],
        opportunities=[],
        unresolved_questions=[],
        missing_information=[],
        strategic_insights=[],
        recommendations=[],
        gaps=[],
    )


def _validate_package_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("path must be a safe package-relative path")
