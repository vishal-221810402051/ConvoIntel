# Backend Phase 6: General Decision Intelligence and Evidence Grounding

## Objective

Backend Phase 6 creates structured, evidence-grounded meeting intelligence from the validated Phase 5 cleaned transcript.

The phase is general purpose. It does not contain Aivancity, GTU, Center of Excellence, curriculum, healthcare, sales, legal, or any other mission profile.

## Scope

Phase 6 implements:

* full Phase 1-5 artifact-chain validation;
* provider-isolated decision-intelligence extraction;
* OpenAI Responses API integration;
* strict structured output;
* general meeting-intelligence prompt rules;
* local evidence resolution;
* local actor and deadline validation;
* local canonical IDs;
* local gap derivation;
* canonical intelligence and metadata artifacts;
* atomic publication, rollback, and idempotent reuse.

## Exclusions

Phase 6 does not implement transcript cleanup changes, audio processing, retranscription, participant identity resolution, date normalization, timezone resolution, calendar sync, reminders, PDF or Markdown reports, dashboards, search, databases, API endpoints, job queues, Android behavior, discovery, mission profiles, embeddings, vector stores, external retrieval, secondary judging, or repair mode.

## Phase 5 Input Contract

The service accepts only a `meeting_id` and resolves it beneath `settings.meetings_dir`.

Before any provider request, it validates:

* `metadata/meeting.json`;
* `metadata/normalization.json`;
* `metadata/transcription.json`;
* `metadata/cleanup.json`;
* `normalized/audio.wav`;
* `transcript/raw.json`;
* `transcript/raw.txt`;
* `transcript/cleaned.json`;
* `transcript/cleaned.txt`.

It verifies meeting IDs, canonical relative paths, normalization provenance, transcription provenance, cleanup provenance, artifact sizes, SHA-256 hashes, deterministic raw and cleaned text rendering, cleaned/raw segment mapping, speaker labels, segment order, raw segment hashes, changed counts, Phase 5 model, Phase 5 prompt version, `store=false`, and protected-token fidelity.

Phase 1-5 input files are never modified by Phase 6.

## Model And Provider

Phase 6 uses the pinned model:

```text
gpt-5-mini-2025-08-07
```

The prompt version is:

```text
convointel-general-intelligence-v1
```

The response schema name is:

```text
convointel_general_intelligence_v1
```

The OpenAI provider uses `client.responses.create(...)` with `store=false`, `tools=[]`, `stream=false`, `background=false`, reasoning effort `low`, strict JSON Schema output, bounded `max_output_tokens`, configured timeout, and configured SDK retries.

The provider does not use Chat Completions, Assistants, raw HTTP, JSON object mode, function calling, tools, web search, file search, embeddings, previous response IDs, conversation objects, or a second model call.

## Prompt-Injection Handling

The cleaned transcript is sent as untrusted data behind an explicit boundary. Transcript text can describe instructions, secrets, prompts, or requests to ignore the schema, but those statements are meeting content only. The model task is defined only by the developer instruction.

## Semantic Categories

The artifact contains:

* executive summary and key outcomes;
* discussion areas;
* decisions;
* action items;
* commitments;
* follow-ups;
* stakeholders;
* risks;
* blockers;
* dependencies;
* opportunities;
* unresolved questions;
* missing information;
* strategic insights;
* recommendations;
* locally derived gaps.

Decisions are accepted, rejected, deferred, provisional, or otherwise settled choices. Proposals remain proposals unless the transcript shows acceptance or settlement.

Action items are concrete work someone must perform. Commitments are explicit promises or obligations by an actor. Follow-ups are communication, coordination, review, confirmation, or check-in activities.

Risks are possible negative events or conditions. Blockers are issues currently preventing or materially stopping progress.

Strategic insights and recommendations must stay separate from participant decisions and must cite transcript evidence.

## Actor Contract

Actors use one of:

```text
speaker_label
named_person
role
team
organization
unknown
```

Speaker labels must exist in the cleaned transcript. Named people, roles, teams, and organizations must occur in the evidence text using deterministic case-insensitive matching. Unknown actors use `null`.

The service does not resolve pronouns to named people, map people to contacts, infer the current application user, or infer speaker identities.

## Deadline Contract

Deadlines use one of:

```text
explicit
ambiguous
missing
not_applicable
```

Explicit and ambiguous deadlines must include exact transcript wording in `text`. Missing and not-applicable deadlines must use `null`.

Phase 6 preserves relative expressions such as `by Friday` verbatim. It does not normalize dates, add years, add timezones, calculate calendar dates, or infer deadlines from meeting dates.

## Evidence Grounding

The provider returns only `evidence_segment_ids`. Every nonempty factual item must have at least one valid segment ID. IDs must exist in the cleaned transcript, be unique within the item, and follow transcript order.

The local service resolves trusted evidence references from the cleaned transcript:

```json
{
  "segment_id": "seg_001",
  "speaker_label": "A",
  "start_seconds": 0.0,
  "end_seconds": 5.2,
  "cleaned_text_sha256": "<sha256>"
}
```

The provider cannot supply canonical evidence objects, timestamps, speakers, quotations, or canonical IDs.

## Canonical IDs

IDs are assigned locally after validation using deterministic three-digit sequences:

```text
outcome_001
discussion_001
decision_001
action_001
commitment_001
follow_up_001
stakeholder_001
risk_001
blocker_001
dependency_001
opportunity_001
question_001
missing_info_001
insight_001
recommendation_001
gap_001
```

Provider output cannot include or control these IDs.

## Local Gap Derivation

The model does not return gaps. The service derives:

* `missing_owner` for action items and follow-ups with unknown owners;
* `missing_deadline` for action items, commitments, and follow-ups with missing deadlines;
* `ambiguous_deadline` for ambiguous deadline references;
* `missing_information` from validated missing-information items.

Gap evidence is copied from the related canonical item.

## Input Limits

The provider input is deterministic compact JSON containing only:

* meeting ID;
* local segment order;
* cleaned transcript segment IDs;
* speaker labels;
* timestamps;
* cleaned segment text.

It is serialized as UTF-8 with `ensure_ascii=False`, sorted keys, and compact separators. The default maximum is 500000 characters. Inputs above the configured limit raise `IntelligenceInputTooLargeError` before any provider request. Phase 6 does not truncate, split, omit, or partially analyze transcript content.

## Artifact Layout

Phase 6 writes:

```text
intelligence/decision_intelligence.json
metadata/intelligence.json
```

It does not create intelligence text, Markdown, PDF, HTML, calendar, timeline, search, database, or index artifacts.

## Metadata And Usage

Metadata records provider name, endpoint, model, prompt version, response format, schema, strict-schema flag, `store=false`, reasoning effort, input artifact paths, input sizes and hashes, segment count, speaker labels, output size and hash, category counts, processing limits, validation flags, provider request count, and token usage when returned.

It does not persist API keys, response IDs, request IDs, raw SDK objects, provider response bodies, full prompts, full requests, environment variables, absolute paths, source audio content, current-user identity, normalized dates, or costs.

## Atomicity And Rollback

The service stages artifacts under:

```text
<meeting_dir>/.staging/intelligence_<uuid>/
```

It writes staged intelligence first, calculates size and hash, writes staged metadata, publishes `intelligence/decision_intelligence.json`, and publishes `metadata/intelligence.json` last.

If publication fails after the intelligence artifact is moved, rollback removes the published intelligence artifact. Staging is removed after success or failure. Rollback failure is logged without hiding the original exception.

## Idempotency

When both final artifacts exist, the service validates typed models, meeting ID, prompt version, provider model, schema, `store=false`, reasoning effort, input hashes, output hash, source hashes, category counts, ID sequences, evidence, actors, deadlines, locally derived gaps, and item limits.

A valid result is reused without a provider request. Partial or inconsistent Phase 6 state raises `IntelligenceStateError` and is not regenerated automatically.

## Live Validation

Runtime validation uses a synthetic complete Phase 1-5 meeting package in `%TEMP%\convointel-phase6-manual`. It performs exactly one real Phase 6 intelligence request, then calls the service again to verify reuse without a second request.

The fixture covers a confirmed decision, a deferred or provisional decision, named and unknown action owners, explicit and missing deadlines, a commitment, a follow-up, a stakeholder position, a risk, a blocker, a dependency, an opportunity, an unresolved question, missing information, and a prompt-injection attempt.

The validation checks category presence, evidence IDs, local evidence references, actor grounding, verbatim `by Friday`, no normalized dates, local gap derivation, prompt-injection rejection, artifact and metadata hashes, staging cleanup, and unchanged Phase 1-5 inputs.

## Limitations

Phase 6 performs one-request intelligence only. Long-transcript map-reduce, temporal interpretation, reminders, calendar recommendations, contact resolution, report generation, database persistence, search indexing, and mission-specific analysis remain later-phase work.

## Handoff

The later temporal phase may consume the structured intelligence and verbatim deadline text after Phase 6 is formally reviewed, runtime validated, committed, and pushed.
