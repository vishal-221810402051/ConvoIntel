# Backend Phase 3: Audio Normalization and Integrity Validation

## Objective

Backend Phase 3 converts a completed Phase 2 meeting package source file into a canonical downstream WAV artifact and records verified normalization metadata.

## Scope

This phase loads `<data_dir>/meetings/<meeting_id>/metadata/meeting.json`, verifies the preserved source file against the Phase 2 size and SHA-256, runs FFmpeg into a staging directory, validates the staged output with FFprobe JSON, records output size and SHA-256, and publishes `normalized/audio.wav` plus `metadata/normalization.json`.

## Exclusions

Phase 3 does not add upload APIs, Android sync, databases, transcription, OpenAI calls, diarization, transcript cleanup, summaries, decisions, action extraction, date extraction, calendar logic, reporting, search, background orchestration, noise reduction, gain normalization, loudness normalization, voice activity detection, or silence removal.

## Dependency Requirements

FFmpeg and FFprobe must be installed as external runtime tools. They are not Python packages.

Verify discovery from PowerShell:

```powershell
ffmpeg -version
ffprobe -version
```

Both commands must return version information. `CONVOINTEL_FFMPEG_BINARY` and `CONVOINTEL_FFPROBE_BINARY` may override executable names when needed.

## Canonical Profile

Profile identifier:

```text
convointel-stt-wav-v1
```

Profile contract:

| Field | Value |
| --- | --- |
| Container | `wav` |
| Codec | `pcm_s16le` |
| Channels | `1` |
| Sample rate | `16000` |
| Sample format | `s16` |

This is a technical audio format profile, not a transcription model.

## Command Profile

FFmpeg is invoked with an argument list and `shell=False`:

```text
ffmpeg -nostdin -hide_banner -loglevel error -y -i <source> -map 0:a:0 -vn -map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le -f wav <staged-output>
```

The service writes only to a unique staging path such as:

```text
<meeting_dir>/.staging/normalization_<uuid>/audio.wav
```

FFprobe validation uses JSON output:

```text
ffprobe -v error -select_streams a -show_entries stream=index,codec_name,sample_fmt,sample_rate,channels -show_entries format=format_name,duration,size -of json <staged-output>
```

## Source Integrity Checks

Before FFmpeg runs, the service:

* validates the meeting ID format;
* resolves the meeting directory beneath `Settings.meetings_dir`;
* loads and validates `metadata/meeting.json` through `MeetingManifest`;
* confirms the requested meeting ID matches the manifest;
* resolves the manifest source path as package-relative only;
* confirms the source file exists and is a regular file;
* recalculates source size;
* recalculates source SHA-256;
* rejects any source whose bytes no longer match Phase 2 metadata.

The original source audio and `metadata/meeting.json` are never modified by Phase 3.

## Package Layout

Input:

```text
<data_dir>/meetings/<meeting_id>/source/original.<m4a|mp3|wav>
<data_dir>/meetings/<meeting_id>/metadata/meeting.json
```

Output:

```text
<data_dir>/meetings/<meeting_id>/
  source/
    original.<extension>
  normalized/
    audio.wav
  metadata/
    meeting.json
    normalization.json
```

## Normalization Metadata

`metadata/normalization.json` is UTF-8 JSON with stable indentation and a trailing newline.

```json
{
  "schema_version": "1.0",
  "meeting_id": "mtg_20260720T153045123456Z_a1b2c3d4",
  "created_at_utc": "2026-07-20T15:45:00.654321Z",
  "status": "normalization_completed",
  "profile": {
    "profile_id": "convointel-stt-wav-v1",
    "container": "wav",
    "codec": "pcm_s16le",
    "sample_rate_hz": 16000,
    "channels": 1,
    "sample_format": "s16"
  },
  "input": {
    "relative_path": "source/original.wav",
    "size_bytes": 123,
    "sha256": "64 lowercase hexadecimal characters"
  },
  "output": {
    "relative_path": "normalized/audio.wav",
    "media_type": "audio/wav",
    "size_bytes": 456,
    "sha256": "64 lowercase hexadecimal characters",
    "duration_seconds": 1.0,
    "codec": "pcm_s16le",
    "sample_rate_hz": 16000,
    "channels": 1,
    "sample_format": "s16"
  },
  "tool": {
    "name": "ffmpeg",
    "version": "ffmpeg version 8.1.2-full_build-www.gyan.dev"
  }
}
```

Persisted paths are package-relative and use forward slashes. Metadata does not store absolute paths, user names, commands, environment variables, transcript fields, or future-stage placeholders.

## Idempotency Policy

If both `normalized/audio.wav` and `metadata/normalization.json` exist, the service validates:

* metadata schema;
* meeting ID;
* input relative path, size, and SHA-256;
* canonical profile;
* output file size and SHA-256;
* FFprobe output format.

When every check passes, the service returns the existing result with `reused_existing=true` and does not rerun FFmpeg.

## Inconsistent-State Policy

If only one final artifact exists, if metadata and output disagree, or if the profile does not match the current canonical profile, the service raises `NormalizationStateError`. It does not silently delete, repair, overwrite, or force-normalize inconsistent packages.

## Atomicity And Rollback

New work is staged under the meeting package:

```text
<meeting_dir>/.staging/normalization_<uuid>/
```

The service writes the WAV and metadata in staging first, then publishes the output and metadata. If metadata publication fails after output publication, the newly published output is removed. Cleanup errors are logged without hiding the original failure.

## Error Model

Phase 3 defines:

* `AudioNormalizationError`
* `MeetingPackageNotFoundError`
* `MeetingManifestNotFoundError`
* `MeetingManifestInvalidError`
* `SourceAudioIntegrityError`
* `SourceAudioMissingError`
* `FfmpegNotAvailableError`
* `FfprobeNotAvailableError`
* `NormalizationTimeoutError`
* `NormalizationProcessError`
* `NormalizedAudioValidationError`
* `NormalizationStateError`
* `NormalizationMetadataWriteError`
* `NormalizationPublicationError`

No HTTP exception handlers are registered in Phase 3 because no normalization endpoint exists yet.

## Unit Validation Commands

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -W error -ra
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall backend
rg -n "(^from app\.|^import app\.|legacy_reference)" backend
```

Expected results:

* all tests pass with warnings promoted to errors;
* no broken Python requirements;
* no syntax errors;
* no legacy runtime imports.

## Real Format Procedure

Use `.test-artifacts/manual-normalization/` for temporary files. Generate a one-second WAV with Python's `wave` module, derive M4A and MP3 samples with real FFmpeg, then for each input:

1. call `AudioIntakeService.intake_audio`;
2. call `AudioNormalizationService.normalize_meeting`;
3. inspect `normalized/audio.wav` with real FFprobe;
4. call normalization a second time and confirm `reused_existing=true`.

Expected output for `.wav`, `.m4a`, and `.mp3`:

* one audio stream;
* WAV container;
* `pcm_s16le`;
* `sample_rate=16000`;
* `channels=1`;
* positive output size;
* non-negative duration;
* metadata checksum matches the output file;
* source checksum still matches the Phase 2 manifest;
* no residual `.staging` directory.

## Known Limitation

Phase 3 standardizes technical format only. It does not improve noisy audio or decide whether speech quality is good enough for transcription.

## Future Handoff

Phase 4 may consume `normalized/audio.wav` for transcription after Phase 3 has passed formal review, runtime validation, commit, and push.
