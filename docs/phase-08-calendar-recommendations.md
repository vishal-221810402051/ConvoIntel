# Backend Phase 8 - Calendar Recommendations

## Objective

Phase 8 turns completed Phase 6 decision intelligence and Phase 7 temporal intelligence into deterministic, reviewable calendar recommendations. It publishes:

```text
calendar/recommendations.json
metadata/calendar_recommendations.json
```

The phase is local-only. It does not talk to a calendar service, create events, send reminders, or use a model.

## Scope

Implemented scope includes full Phase 1-7 validation, deterministic candidate grouping, recommendation classification, schedule construction, readiness derivation, review and blocking reasons, informational flags, exact deduplication, exclusions, canonical recommendation JSON, metadata, atomic publication, rollback, and idempotent reuse.

## Exclusions

Phase 8 does not implement calendar synchronization, event creation, event updates, event deletion, availability lookup, attendee invitations, reminders, notifications, background jobs, ICS export, reports, endpoints, databases, Android work, discovery, orchestration, archive indexing, search, embeddings, provider calls, or mission-specific profiles.

## Provider-Free Design

Phase 6 already supplies semantic meeting intelligence. Phase 7 already supplies trusted temporal expressions, normalized components, UTC values when available, recurrence descriptors, evidence, temporal gaps, and explicit links to Phase 6 items.

Phase 8 only projects those trusted artifacts into a calendar-review contract. It does not check for an API key, instantiate a model client, perform HTTP work, or add a provider boundary.

## Input Contracts

The service accepts only:

```python
generate_calendar_recommendations(meeting_id)
```

It resolves the package under `settings.meetings_dir` and validates the existing Phase 1-7 chain through the public Phase 6 and Phase 7 services. It requires:

```text
metadata/meeting.json
metadata/normalization.json
metadata/transcription.json
metadata/cleanup.json
metadata/intelligence.json
metadata/temporal.json
normalized/audio.wav
transcript/raw.json
transcript/raw.txt
transcript/cleaned.json
transcript/cleaned.txt
intelligence/decision_intelligence.json
temporal/temporal_intelligence.json
```

Any missing, partial, malformed, or hash-inconsistent upstream state prevents recommendation generation.

## Recommendation Types

Phase 8 emits:

```text
event
deadline
milestone
recurring_event
reminder_request
```

Durations by themselves, unsupported temporal categories, and non-actionable temporal references are recorded as exclusions instead of silently disappearing.

## Schedule Shapes

Schedules use:

```text
all_day
timed
point_in_time
recurring
unscheduled
```

Date-only deadlines and milestones are all-day and do not get midnight UTC values. Timed events require start and end values. Deadlines and milestones with times use point-in-time schedules. Recurrences remain descriptive. Ambiguous, unresolved, incomplete, or conflicting candidates remain unscheduled.

## Readiness

Readiness statuses are:

```text
ready
needs_review
blocked
```

`ready` means ready to present for human review. It does not mean approved, synchronized, scheduled, or executable.

Review reasons include ambiguous or unresolved temporal data, missing start date, missing start time, missing end, missing timezone, no related intelligence, recurrence missing anchor, recurrence missing time, unresolved reminder trigger, and partial temporal information.

Blocking reasons include conflicting temporal information, incompatible temporal components, multiple distinct schedules, and invalid schedule shape.

Informational flags include duplicate source merging, end derived from duration, date-only candidate, standalone temporal candidate, and multiple evidence segments.

## Titles And Descriptions

Titles are deterministic:

```text
<Prefix>: <trusted source text>
```

Trusted source text comes only from Phase 6 fields for linked items, or the exact Phase 7 `expression_text` for standalone temporal items. Titles normalize whitespace, preserve identifiers and proper nouns, and truncate at a stable 500-character boundary.

Descriptions preserve provenance without duplicating transcripts:

```text
Source type: <type>
Source ID: <id or standalone>
Source statement: <complete trusted source text>
Temporal expression: <exact expression text>
```

When multiple temporal items combine, descriptions list each expression deterministically.

## Explicit Relationship Rule

Phase 8 uses only Phase 7 `related_intelligence_items`. It never infers a Phase 6 relationship from evidence overlap, similar text, speaker identity, proximity, matching dates, actor names, or deadline wording.

One temporal item linked to multiple Phase 6 items creates separate recommendation contexts. Empty link lists remain standalone.

## Candidate Grouping

Candidates are grouped by explicit Phase 6 reference. Contexts with different references never combine. Standalone temporal items do not combine except through exact deduplication. Reminder requests stay separate from events.

Compatible components for one explicit reference may include date, start time, end time, time window, duration, recurrence, deadline, or milestone information. Ambiguous and resolved values are not silently treated as the same resolved schedule.

## Duration-Assisted End Calculation

A duration may derive an event end only when it is explicitly linked to the same Phase 6 item as the event start, is positive, has seconds, and no conflicting explicit end exists.

The service calculates:

```text
end = start + duration
```

It records `end_derived_from_duration`. Months, years, vague durations, unrelated durations, and evidence-overlap-only durations do not derive an end.

## Recurrence Handling

Recurrences keep Phase 7 descriptive fields: frequency, interval, and days. Phase 8 does not create recurrence rules. Missing anchor date or required time yields `needs_review`.

## Reminder Requests

Reminder requests are recommendations, not scheduled alarms. Phase 8 preserves the exact reminder expression. Resolved triggers may use point-in-time or all-day schedules; unresolved triggers remain unscheduled with `reminder_trigger_unresolved`.

## Conflict Handling

Conflicts block the candidate instead of selecting a value. Conflicts include distinct exact dates, distinct exact start times, distinct deadlines, incompatible windows, incompatible durations, and Phase 7 conflict gaps.

Blocked recommendations preserve source temporal IDs and evidence and use an unscheduled shape when no defensible schedule can be selected.

## Exact Deduplication

Deduplication uses a deterministic SHA-256 fingerprint over recommendation type, schedule fields, UTC values, recurrence, reminder expression, explicit intelligence reference, and standalone expression text when no reference exists.

It does not use fuzzy matching, similar titles, shared evidence alone, shared actor, or same date/time with different intelligence sources. Exact duplicates merge source temporal IDs and evidence in source order and record `duplicate_sources_merged`.

## Exclusions

Exclusions use local IDs:

```text
calendar_exclusion_001
calendar_exclusion_002
```

They preserve temporal source IDs, evidence, a deterministic reason, and a short description.

## Artifacts And Metadata

The canonical artifact stores schema version, meeting ID, source hashes, generator version `convointel-calendar-recommendations-v1`, recommendations, and exclusions.

Metadata stores deterministic-local generator details, input sizes and hashes, temporal counts, output size and hash, recommendation counts, readiness counts, exclusion count, merged-source count, and processing validation flags.

Metadata records `network_access=false` and `provider_request_count=0`. It does not include usage, model fields, API-key state, calendar identifiers, event identifiers, attendee data, reminder configuration, or absolute paths.

## Atomicity And Rollback

Phase 8 stages both output files under:

```text
<meeting_dir>/.staging/calendar_recommendations_<uuid>/
```

The recommendation JSON is published before metadata. If metadata publication fails, the recommendation JSON is removed. Staging is removed after success or failure. Phase 1-7 artifacts are never overwritten.

## Idempotency

When both Phase 8 artifacts already exist, the service validates typed models, meeting ID, generator version, deterministic mode, network flag, local generator count, input hashes, output hash, counts, IDs, mapping, schedules, readiness, deduplication keys, merged-source counts, and exclusions.

Valid output returns `reused_existing=true`. Partial or inconsistent output raises `CalendarStateError` and is not regenerated automatically.

## Manual Runtime Validation

Manual validation uses a temporary synthetic package and fake upstream providers to build Phase 1-7 artifacts, then runs Phase 8 twice. It verifies recommendation IDs, exclusion IDs, deterministic titles and descriptions, ready date-only deadline, ready timed workshop, duration-derived end, descriptive recurrence, unscheduled reminder request, ambiguous milestone, blocked conflict, exact duplicate merge, evidence preservation, explicit relationships, hashes, metadata, reuse, and cleanup.

## Limitations And Handoff

Phase 8 stops at local recommendation generation. A later integration phase may decide how reviewed recommendations map to a real calendar system, but that work is intentionally outside this phase.
