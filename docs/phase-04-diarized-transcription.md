# Backend Phase 4: Diarized Raw Transcription and Provider Boundary

## Objective

Backend Phase 4 turns a validated Phase 3 normalized WAV artifact into provider-independent raw transcript artifacts with anonymous speaker labels and timestamped segments.

## Scope

This phase verifies the Phase 2 and Phase 3 package metadata, sends only `normalized/audio.wav` to an injected transcription provider, maps the diarized response into canonical schemas, writes `transcript/raw.json`, writes deterministic `transcript/raw.txt`, and records `metadata/transcription.json`.

## Exclusions

Phase 4 does not add cleanup, spelling correction, grammar correction, summaries, decisions, action items, owner resolution, speaker identity resolution, known-speaker enrollment, date extraction, gap analysis, calendar logic, reports, uploads, Android sync, background workers, dashboards, search, local Whisper, streaming, or Phase 5 work.

## OpenAI Dependency

The runtime dependency is the official OpenAI Python SDK:

```text
openai>=2.46,<3
```

No unofficial wrappers, LangChain, retry libraries, or local speech-recognition packages are used.

## API-Key Configuration

The OpenAI API key is read from either:

```text
OPENAI_API_KEY
CONVOINTEL_OPENAI_API_KEY
```

The key is stored in settings as `SecretStr`, is not shown in `repr`, and is never serialized into transcript artifacts or logs. `.env.example` contains only empty placeholders.

## Default Model

Phase 4 fixes the model to:

```text
gpt-4o-transcribe-diarize
```

Arbitrary model names are rejected because artifact semantics depend on diarized segments.

## Provider Boundary

`backend.app.services.transcription.provider` defines provider-independent request/result structures and the `TranscriptionProvider` protocol.

`backend.app.services.transcription.openai_provider` constructs the OpenAI client, sends the SDK request, maps SDK response fields, and translates SDK exceptions.

`backend.app.services.transcription.service` owns package validation, staging, canonical artifact generation, publication, reuse, and rollback.

## Request Contract

The OpenAI provider calls:

```text
client.audio.transcriptions.create(...)
```

with:

```text
file=<opened normalized/audio.wav>
model=gpt-4o-transcribe-diarize
response_format=diarized_json
chunking_strategy=auto
stream=False
language=<optional two-letter code>
```

No prompt, known-speaker references, raw HTTP request, Responses API call, or streaming path is used.

## Why `diarized_json`

`diarized_json` preserves combined text plus timestamped speaker segments in one provider response, allowing Convointel to persist raw speaker-aware artifacts without inferring identities.

## Anonymous Speaker Labels

Labels such as `A` and `B` are provider speaker labels only. They are not participant identities and are not mapped to real people in Phase 4.

## Package Layout

After Phase 4:

```text
<meeting_id>/
  source/
    original.<extension>
  normalized/
    audio.wav
  transcript/
    raw.json
    raw.txt
  metadata/
    meeting.json
    normalization.json
    transcription.json
```

## Raw JSON Schema

`transcript/raw.json` stores:

```json
{
  "schema_version": "1.0",
  "meeting_id": "mtg_20260720T153045123456Z_a1b2c3d4",
  "text": "Complete provider transcription text",
  "duration_seconds": 42.7,
  "segments": [
    {
      "segment_id": "seg_001",
      "start_seconds": 0.0,
      "end_seconds": 5.2,
      "speaker_label": "A",
      "text": "Segment text"
    }
  ]
}
```

Segments must have unique IDs, non-negative timestamps, ordered starts, no meaningful overlap beyond the documented tolerance, nonempty anonymous speaker labels, and string text.

## Raw Text Format

`transcript/raw.txt` is rendered deterministically:

```text
[00:00.000-00:05.200] Speaker A: Segment text
[00:05.200-00:12.800] Speaker B: Segment text
```

If there are no segments but combined text is nonempty, raw text contains the provider text plus a final newline. If both are empty, it contains only a newline.

## Metadata Schema

`metadata/transcription.json` records provider, model, request shape, normalized input provenance, raw artifact paths, sizes, SHA-256 hashes, segment count, speaker labels, and optional usage.

Usage may be duration-based:

```json
{"type": "duration", "seconds": 43}
```

or token-based:

```json
{"type": "tokens", "input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
```

No pricing is calculated.

## Input Integrity

Before a provider call, the service verifies:

* meeting ID syntax;
* package path below `Settings.meetings_dir`;
* `metadata/meeting.json`;
* `metadata/normalization.json`;
* matching meeting IDs;
* `convointel-stt-wav-v1` profile;
* canonical `normalized/audio.wav` path;
* normalized audio file existence;
* normalized size and SHA-256;
* normalization input checksum against the Phase 2 manifest;
* mono, 16 kHz, `pcm_s16le`, `s16` metadata.

OpenAI is not called when integrity fails.

## Atomicity

New work is staged under:

```text
<meeting_dir>/.staging/transcription_<uuid>/
```

The service writes staged `raw.json`, `raw.txt`, and `transcription.json`, then publishes the transcript directory followed by metadata. If metadata publication fails after transcript publication, the transcript directory is removed.

## Idempotency

If `transcript/raw.json`, `transcript/raw.txt`, and `metadata/transcription.json` all exist, the service validates metadata, checksums, segment count, speaker labels, model, provider, input provenance, and deterministic raw-text rendering. Valid completed results return `reused_existing=true` without calling the provider.

Partial or inconsistent states raise `TranscriptionStateError` and are not repaired or overwritten.

## Error Model

Phase 4 defines typed configuration, provider, input-integrity, state, metadata-write, and publication errors under `backend.app.services.transcription.errors`. No HTTP handlers are registered because there is no transcription endpoint yet.

## Unit Tests

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -W error -ra
```

Unit tests use fake providers and injected SDK clients. They do not make real OpenAI calls.

## Live API Validation

Use `.test-artifacts/manual-transcription/` for the live fixture. Generate a short non-sensitive spoken WAV, run Phase 2 intake, Phase 3 normalization, and Phase 4 transcription exactly once with a real key. Validate text, segments, speaker labels, timestamps, artifact hashes, reuse, and staging cleanup.

## Cost Awareness

The live validation performs exactly one successful API transcription request. Injected providers cover failure modes to avoid repeated billable calls.

## Limitations

Speaker labels are not identities. Phase 4 does not clean, summarize, correct, classify, extract actions, or infer meeting intelligence.

## Future Handoff

Phase 5 may consume `transcript/raw.json` and `transcript/raw.txt` for transcript cleanup after Phase 4 is formally reviewed, validated, committed, and pushed.
