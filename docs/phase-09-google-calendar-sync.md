# Backend Phase 9 - Google Calendar Sync

## Objective

Phase 9 syncs exactly one explicitly approved Phase 8 calendar recommendation to Google Calendar. It creates or reuses a deterministic Google event owned by the authenticated operator.

The phase publishes:

```text
calendar/sync/<recommendation_id>.json
metadata/calendar_sync/<recommendation_id>.json
metadata/calendar_sync_attempts/<recommendation_id>/<attempt_id>.json
```

Attempt records are written only for safe failure recovery. They do not contain credentials, headers, raw provider payloads, or transcript text.

## Scope

Implemented scope includes full Phase 1-8 validation, runtime-only approval, recommendation eligibility checks, deterministic Google event payload construction, OAuth token loading and refresh, official Calendar v3 create-only gateway calls, duplicate recovery by deterministic event ID, remote provenance validation, local atomic publication, and idempotent reuse.

## Exclusions

Phase 9 does not implement batch sync, automatic approval, recommendation inference, event updates, event deletion, event moves, calendar creation, invites, conferencing, availability lookup, reminders beyond Google defaults, reports, endpoints, databases, Android work, background jobs, search, or mission-specific profiles.

## Authorization

Sync never launches a browser. Authorization is a separate setup step:

```powershell
$env:CONVOINTEL_GOOGLE_CALENDAR_CLIENT_SECRET_PATH = "<external-oauth-client-json>"
$env:CONVOINTEL_GOOGLE_CALENDAR_TOKEN_PATH = "<external-token-json>"
.\.venv\Scripts\python.exe scripts\google_calendar_auth.py
```

The bootstrap script uses exactly this OAuth scope:

```text
https://www.googleapis.com/auth/calendar.events.owned
```

Only calendars owned by the authenticated operator are supported. A token created
with a previous or different scope contract must be discarded, and authorization
must be run again.

The token path defaults to:

```text
<data_dir>/auth/google_calendar_token.json
```

OAuth client files and token files must not be committed.

## Approval Contract

The sync entry point accepts one approval object:

```python
CalendarSyncApproval(
    recommendation_id="calendar_rec_001",
    confirmed=True,
)
```

`confirmed` has no default. Only the literal boolean `True` is accepted. Approval source is persisted as `explicit_runtime`, and the service records an audit timestamp when local success artifacts are written.

## Eligibility Contract

The service validates the existing Phase 8 recommendation artifact before any Google access. A recommendation is syncable only when it:

```text
exists
is ready
has no review reasons
has no blocking reasons
has a supported schedule
has explicit runtime approval
```

Supported schedules are all-day and timed events, all-day deadlines and milestones, point-in-time deadlines or milestones only when a complete explicit end exists, and recurring events with complete first-instance timing plus supported recurrence.

Reminder requests, unscheduled items, incomplete timed schedules, missing timezones, ambiguous recurrence, nonpositive durations, and review or blocked recommendations are rejected before remote access.

## Google Event Contract

The event summary is the exact Phase 8 title. The description contains the Phase 8 description plus:

```text
Convointel meeting ID: <meeting_id>
Convointel recommendation ID: <recommendation_id>
```

The payload does not include attendees, conferencing, location, credential data, absolute paths, raw transcript text, or custom reminder overrides. Google default reminders are used.

Private extended properties store only Convointel provenance:

```text
convointel_meeting_id
convointel_recommendation_id
convointel_recommendation_hash
convointel_sync_version
```

## Duplicate Recovery

The Google event ID is deterministic:

```text
convointel + first 48 hex characters of SHA-256(sync_version, calendar_id, meeting_id, recommendation_id, recommendations_sha256, deduplication_key_sha256)
```

The service checks that event ID before insert. If it exists and the private provenance matches, it is reused. If insert reports a duplicate, the service gets the same event ID and reuses it only when provenance still matches.

Remote title or schedule edits are not overwritten. Phase 9 never calls update, patch, delete, move, calendar creation, invite, or conferencing operations.

## Local Atomicity

Successful sync state is staged under:

```text
<meeting_dir>/.staging/google_calendar_sync_<recommendation_id>_<uuid>/
```

The success artifact is published before metadata. If metadata publication fails after remote success, the local success artifact is removed and a safe attempt record is written if possible. The remote event is not deleted.

## Idempotency

When both Phase 9 local files already exist, the service validates typed models, source sizes and hashes, approval source, calendar ID, payload checksum, deterministic event ID, metadata output hash, and remote private provenance. Valid local state returns runtime reuse without creating another event.

Partial local state is rejected without remote insert.
