# Backend Phase 2: Canonical Meeting Package and Local Audio Intake

## Objective

Backend Phase 2 adds the first Convointel backend domain capability: preserving a local source-audio file inside a canonical meeting package with typed metadata and rollback-safe publication.

## Scope

This phase accepts local `.m4a`, `.mp3`, and `.wav` paths, validates basic filesystem properties, generates a canonical meeting ID, copies the original bytes, computes size and SHA-256 provenance, writes `metadata/meeting.json`, and returns a typed runtime result.

## Exclusions

This phase does not add upload endpoints, Android synchronization, discovery, pairing, FFmpeg, audio decoding, normalization, transcription, OpenAI calls, transcript cleanup, summaries, intelligence extraction, date or action extraction, calendar logic, reporting, database schemas, background processing, dashboards, search, or cloud storage.

## Architecture

`backend.app.models.meeting` defines the Pydantic manifest and runtime result models.

`backend.app.services.audio.errors` defines precise intake-domain errors.

`backend.app.services.audio.intake` implements the local intake service using the existing `Settings` object and the configured data directory.

`backend.app.config.Settings.meetings_dir` derives the meeting root from `CONVOINTEL_DATA_DIR`; no new environment variable is introduced.

## Package Layout

Meeting packages are written under:

```text
<data_dir>/meetings/<meeting_id>/
```

Phase 2 creates only:

```text
<meeting_id>/
  source/
    original.<extension>
  metadata/
    meeting.json
```

Incomplete packages are staged under:

```text
<data_dir>/meetings/.staging/
```

Future directories such as transcripts, reports, normalized audio, calendar data, and intelligence artifacts are not created in Phase 2.

## Manifest Schema

`metadata/meeting.json` is UTF-8 JSON with stable indentation and a trailing newline.

```json
{
  "schema_version": "1.0",
  "meeting_id": "mtg_20260720T153045123456Z_a1b2c3d4",
  "created_at_utc": "2026-07-20T15:30:45.123456Z",
  "status": "intake_completed",
  "source": {
    "original_filename": "meeting.m4a",
    "stored_filename": "original.m4a",
    "relative_path": "source/original.m4a",
    "extension": ".m4a",
    "media_type": "audio/mp4",
    "size_bytes": 12345,
    "sha256": "64 lowercase hexadecimal characters"
  }
}
```

The manifest stores no absolute source path, no absolute package path, no username, no machine-specific path, no Android identifier, no inferred title, and no placeholders for future artifacts.

## Meeting ID Format

Meeting IDs use:

```text
mtg_<UTC timestamp>_<random suffix>
```

Example:

```text
mtg_20260720T153045123456Z_a1b2c3d4
```

The timestamp is sortable, UTC, colon-free, and filesystem-safe. The random suffix is generated with standard-library cryptographic randomness by default. Tests inject deterministic clocks and suffixes.

## Validation Policy

The service checks that the source path exists, is a regular file, has a supported extension, has a size greater than zero, and can be opened for reading.

Supported extensions and media types are:

| Extension | Media type |
| --- | --- |
| `.m4a` | `audio/mp4` |
| `.mp3` | `audio/mpeg` |
| `.wav` | `audio/wav` |

Extension matching is case-insensitive. Audio stream decoding is not performed in Phase 2. A nonempty file with an accepted extension may pass intake even if a later normalization phase rejects its audio content.

## Atomicity And Rollback

The service validates the source before creating package directories. It stages the package on the same filesystem, copies the source in chunks while computing SHA-256, flushes written files where practical, writes metadata through a temporary file, and publishes the completed package by renaming the staging directory to the final meeting ID.

If copying or metadata writing fails, the staging package is removed. A completed final meeting directory is not published until all Phase 2 files are present.

## Error Model

Phase 2 defines:

* `AudioIntakeError`
* `SourceAudioNotFoundError`
* `SourceAudioNotFileError`
* `UnsupportedAudioFormatError`
* `EmptyAudioFileError`
* `SourceAudioReadError`
* `MeetingIdCollisionError`
* `MeetingPackageWriteError`

No API exception handlers are registered for these errors in Phase 2 because no intake API exists yet.

## Validation Commands

Run from the repository root:

```powershell
git status --short
.\.venv\Scripts\python.exe -m pytest -W error -ra
.\.venv\Scripts\python.exe -m compileall backend
rg -n "(^from app\.|^import app\.|legacy_reference)" backend
```

Runtime health check:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health
```

Expected health response:

```json
{
  "status": "ok",
  "service": "convointel-backend",
  "api_version": "v1"
}
```

## Manual Intake Procedure

Use a temporary data directory under `.test-artifacts/manual-intake/`, generate a small silent WAV with Python's standard-library `wave` module, instantiate `AudioIntakeService` with `Settings(data_dir=<temporary-data-dir>)`, and call `intake_audio(<wav-path>)`.

Expected result:

* a `mtg_...` meeting ID is returned;
* the final meeting directory exists;
* `source/original.wav` exists and matches the input bytes;
* `metadata/meeting.json` validates through `MeetingManifest`;
* SHA-256 and size match the stored source bytes;
* manifest status is `intake_completed`;
* no absolute source path is persisted.

## Future Handoff

Phase 3 may normalize accepted source audio. Phase 2 deliberately preserves original bytes only and does not claim codec validity or transcription readiness.
