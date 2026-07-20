# Backend Phase 7 - Temporal Intelligence

## Objective

Phase 7 extracts trusted, evidence-grounded temporal intelligence from completed Phase 6 meeting packages. It identifies temporal expressions, normalizes supported values locally, links them to transcript evidence and Phase 6 intelligence IDs, and publishes:

```text
temporal/temporal_intelligence.json
metadata/temporal.json
```

## Scope

Implemented scope includes full Phase 1-6 validation, optional explicit runtime reference datetime, a temporal provider protocol, an OpenAI Responses provider, strict JSON Schema output, local IDs, local ISO parsing, local UTC conversion, duration handling, recurrence-as-description, reminder requests as data, conflict detection, temporal gaps, atomic publication, rollback, and idempotent reuse.

## Exclusions

Phase 7 does not implement Google Calendar, calendar sync, event creation, scheduling, reminders, notifications, alarms, background jobs, reports, dashboards, databases, endpoints, Android, discovery, orchestration, search, embeddings, archive indexing, mission profiles, or calendar recommendations.

## Trusted Temporal Reference

`MeetingManifest.created_at_utc` is package intake metadata. It is not a trusted meeting-start datetime and is never used as the meeting temporal reference.

The service accepts:

```python
extract_temporal_intelligence(
    meeting_id,
    reference_datetime=...,
    timezone_name=...,
)
```

`reference_datetime` and `timezone_name` must be supplied together or both omitted. The datetime must be timezone-aware, the timezone must be an IANA name, and the persisted source is `explicit_runtime`. When no reference is supplied, absolute expressions may be preserved and normalized, but relative or deictic expressions remain unresolved. The service does not call `datetime.now()` to infer meeting time.

## Provider Contract

Phase 7 uses the pinned model `gpt-5-mini-2025-08-07`, prompt version `convointel-temporal-intelligence-v1`, response schema `convointel_temporal_intelligence_v1`, Responses API, `store=false`, `tools=[]`, `background=false`, `stream=false`, reasoning effort `low`, and bounded output tokens.

The provider receives compact deterministic JSON prefixed with `UNTRUSTED_TEMPORAL_MEETING_DATA_JSON`. The payload contains only trusted reference data, cleaned transcript segments, and temporal-relevant Phase 6 context. It excludes raw transcripts, source audio, hashes, metadata documents, paths, secrets, user identity, external context, and mission context.

## Prompt Injection

Transcript and Phase 6 intelligence text are untrusted meeting data. Instructions inside meeting content are never followed, including requests to reveal prompts or secrets, ignore schema, invoke tools, or use external information.

## Temporal Contract

Categories: `date_reference`, `time_reference`, `datetime_reference`, `deadline`, `milestone`, `duration`, `time_window`, `recurrence`, `reminder_request`, `other_temporal`.

Expression types: `absolute`, `relative`, `deictic`, `duration`, `recurring`, `range`, `vague`, `unknown`.

Resolution statuses: `resolved_exact`, `resolved_relative`, `ambiguous`, `unresolved`.

The provider returns verbatim `expression_text`, normalized date/time components when supported, duration components, recurrence descriptors, evidence segment IDs, and optional Phase 6 links. It must not return canonical temporal IDs, gap IDs, evidence objects, UTC strings, calendar events, reminder schedules, RRULEs, segment timestamps, speaker metadata, paths, hashes, or new intelligence IDs.

## Normalization

Dates use `YYYY-MM-DD`. Times use `HH:MM` or `HH:MM:SS` without timezone suffixes. Date-only values do not create UTC midnight. UTC datetimes are calculated locally only when date, time, and timezone or explicit numeric offset are present.

The normalizer validates IANA timezone names through `zoneinfo`. The project declares the first-party `tzdata` package so Windows environments have the IANA database available without hand-written timezone rules. DST nonexistent and ambiguous local times are rejected.

Durations in seconds, minutes, hours, days, and weeks convert to seconds. Months and years are retained without fabricated seconds. Recurrences remain descriptive and never become RRULEs. Reminder requests are extracted as data and never scheduled.

## Evidence And Links

Every temporal item must cite ordered, unique cleaned transcript segment IDs. The service resolves local evidence objects with segment ID, speaker label, timestamps, and cleaned-text hash. Full segment text is not duplicated in the canonical temporal artifact.

Phase 6 links must reference existing decisions, action items, commitments, follow-ups, missing-information items, or gaps. Linked item evidence must overlap the temporal evidence.

## Gaps And Conflicts

Phase 6 `missing_deadline` and `ambiguous_deadline` gaps are copied into temporal gaps. The service derives gaps for unresolved expressions, missing references, and conflicting resolved temporal values attached to the same Phase 6 intelligence item. Conflicts are preserved; the service does not silently select one date.

## Artifacts And Metadata

The canonical artifact stores source cleaned transcript, intelligence, and intelligence-metadata hashes; prompt version; optional trusted reference; local temporal IDs; temporal items; and gaps.

Metadata stores provider contract, input sizes and hashes, reference metadata, output size and hash, category counts, processing limits, request count, validation flags, and token usage when reported.

## Input Limit

One provider request is allowed per supported meeting. Input size is calculated from deterministic compact JSON. If the payload exceeds `temporal_max_input_characters`, the service raises `TemporalInputTooLargeError`, does not truncate, and makes no provider call.

## Atomicity And Reuse

Temporal JSON and metadata are written through a staging directory. Metadata is published last. Failures roll back staging and remove partially published temporal artifacts. Valid complete artifacts are reused only when source hashes, provider contract, prompt version, schema, reasoning effort, category counts, validation flags, and the exact canonical reference match.

## Live Validation

Runtime validation uses a synthetic complete Phase 1-6 package under the system temporary directory, an explicit reference of `2026-07-20T10:00:00+02:00` with timezone `Europe/Paris`, exactly one temporal provider request, and a second local reuse call. The temporary package is removed afterward.

## Limitations And Handoff

Phase 7 produces normalized temporal intelligence only. Calendar recommendations, scheduling, reminder automation, attendee invitations, and event creation belong to a later calendar phase.
