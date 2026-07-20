"""Provider boundary for general meeting decision intelligence."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.models.intelligence import (
    ActorKind,
    Confidence,
    DeadlineStatus,
    DecisionStatus,
    InferenceBasis,
    IntelligenceUsage,
    Priority,
    PriorityBasis,
    StakeholderStance,
    WorkStatus,
)

MAX_PROVIDER_ITEMS_PER_CATEGORY = 500


class _ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderActor(_ProviderModel):
    kind: ActorKind
    value: str | None


class ProviderDeadline(_ProviderModel):
    status: DeadlineStatus
    text: str | None


class ProviderKeyOutcome(_ProviderModel):
    statement: str = Field(min_length=1, max_length=1000)
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderExecutiveSummary(_ProviderModel):
    overview: str = Field(max_length=4000)
    evidence_segment_ids: list[str]
    key_outcomes: list[ProviderKeyOutcome] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )


class ProviderDiscussionArea(_ProviderModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=3000)
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderDecision(_ProviderModel):
    statement: str = Field(min_length=1, max_length=2000)
    status: DecisionStatus
    rationale: str | None = Field(max_length=3000)
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderActionItem(_ProviderModel):
    description: str = Field(min_length=1, max_length=2000)
    owner: ProviderActor
    deadline: ProviderDeadline
    priority: Priority
    priority_basis: PriorityBasis
    status: WorkStatus
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderCommitment(_ProviderModel):
    statement: str = Field(min_length=1, max_length=2000)
    actor: ProviderActor
    deadline: ProviderDeadline
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderFollowUp(_ProviderModel):
    description: str = Field(min_length=1, max_length=2000)
    owner: ProviderActor
    deadline: ProviderDeadline
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderStakeholderPosition(_ProviderModel):
    actor: ProviderActor
    position: str = Field(min_length=1, max_length=2000)
    stance: StakeholderStance
    concerns: list[str] = Field(max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY)
    evidence_segment_ids: list[str] = Field(min_length=1)

    @field_validator("concerns")
    @classmethod
    def validate_concerns(cls, value: list[str]) -> list[str]:
        return [_normalize_required_text(item, "concerns") for item in value]


class ProviderRisk(_ProviderModel):
    description: str = Field(min_length=1, max_length=2000)
    severity: Priority
    likelihood: Priority
    basis: InferenceBasis
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderBlocker(_ProviderModel):
    description: str = Field(min_length=1, max_length=2000)
    responsible_actor: ProviderActor
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderDependency(_ProviderModel):
    description: str = Field(min_length=1, max_length=2000)
    dependency_on: ProviderActor
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderOpportunity(_ProviderModel):
    description: str = Field(min_length=1, max_length=2000)
    basis: InferenceBasis
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderUnresolvedQuestion(_ProviderModel):
    question: str = Field(min_length=1, max_length=2000)
    asked_by: ProviderActor
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderMissingInformation(_ProviderModel):
    description: str = Field(min_length=1, max_length=2000)
    required_for: str | None = Field(max_length=2000)
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderStrategicInsight(_ProviderModel):
    insight: str = Field(min_length=1, max_length=3000)
    confidence: Confidence
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderRecommendation(_ProviderModel):
    recommendation: str = Field(min_length=1, max_length=3000)
    priority: Priority
    rationale: str = Field(min_length=1, max_length=3000)
    evidence_segment_ids: list[str] = Field(min_length=1)


class ProviderDecisionIntelligence(_ProviderModel):
    executive_summary: ProviderExecutiveSummary
    discussion_areas: list[ProviderDiscussionArea] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    decisions: list[ProviderDecision] = Field(max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY)
    action_items: list[ProviderActionItem] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    commitments: list[ProviderCommitment] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    follow_ups: list[ProviderFollowUp] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    stakeholders: list[ProviderStakeholderPosition] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    risks: list[ProviderRisk] = Field(max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY)
    blockers: list[ProviderBlocker] = Field(max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY)
    dependencies: list[ProviderDependency] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    opportunities: list[ProviderOpportunity] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    unresolved_questions: list[ProviderUnresolvedQuestion] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    missing_information: list[ProviderMissingInformation] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    strategic_insights: list[ProviderStrategicInsight] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )
    recommendations: list[ProviderRecommendation] = Field(
        max_length=MAX_PROVIDER_ITEMS_PER_CATEGORY
    )


class IntelligenceProviderRequest(BaseModel):
    """Provider-independent intelligence request."""

    meeting_id: str
    model: str
    prompt_version: str
    response_schema_name: str
    reasoning_effort: str
    max_output_tokens: int = Field(gt=0)
    max_items_per_category: int = Field(ge=1, le=500)
    input_character_count: int = Field(ge=0)
    transcript_payload_json: str


class IntelligenceProviderResult(BaseModel):
    """Provider-independent intelligence response."""

    intelligence: ProviderDecisionIntelligence
    usage: IntelligenceUsage | None = None


class IntelligenceProvider(Protocol):
    """Protocol implemented by concrete intelligence providers."""

    def analyze(
        self,
        request: IntelligenceProviderRequest,
    ) -> IntelligenceProviderResult:
        """Analyze one complete cleaned meeting transcript."""


def _normalize_required_text(value: str, field_name: str) -> str:
    normalized = " ".join(str(value).strip().split())
    if not normalized:
        raise ValueError(f"{field_name} entries must be nonempty")
    return normalized
