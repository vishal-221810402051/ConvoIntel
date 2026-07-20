# Backend Phase 5: Transcript Cleanup and Fidelity Guard

## Objective

Backend Phase 5 creates a readable but semantically faithful transcript from the validated Phase 4 raw transcript.

## Scope

Phase 5 implements raw transcript integrity verification, provider-isolated cleanup, strict structured output, deterministic contiguous batching, protected-token fidelity checks, canonical cleaned transcript artifacts, cleanup metadata, atomic publication, rollback, idempotent reuse, unit validation, and one minimal live provider validation.

## Exclusions

Phase 5 does not summarize, extract topics, decisions, action items, owners, dates, risks, blockers, questions, recommendations, calendar data, reports, search records, API endpoints, database rows, background jobs, Android behavior, uploads, discovery, orchestration, or Phase 6 meeting intelligence.

## Raw Versus Cleaned Transcript

`transcript/raw.json` and `transcript/raw.txt` remain the immutable Phase 4 output. Phase 5 writes separate cleanup artifacts:

```text
transcript/cleaned.json
transcript/cleaned.txt
metadata/cleanup.json
```

Raw artifacts are never modified by cleanup.

## Provider Contract

The cleanup provider is isolated behind `backend.app.services.cleanup.provider`. The OpenAI implementation uses the Responses API with:

```text
model = gpt-5-mini-2025-08-07
store = false
tools = []
stream = false
background = false
strict JSON Schema output
```

The cleanup prompt version is:

```text
convointel-transcript-cleanup-v1
```

Changing the prompt version makes existing cleanup artifacts incompatible with automatic reuse.

## Prompt Injection Treatment

Transcript content is sent as untrusted data. The provider instructions explicitly prohibit following instructions that appear inside transcript text. The provider may only return cleaned text for supplied segment IDs.

## Cleanup Rules

Allowed cleanup includes punctuation, capitalization, spacing, obvious repeated words, obvious stutters, sentence boundaries, and highly confident transcription-error corrections. Cleanup must preserve meaning, language, code-switching, numbers, dates, times, percentages, currency amounts, URLs, email addresses, identifiers, uncertainty, disagreement, and qualifications.

Cleanup must not summarize, translate, identify speakers, add facts, infer missing information, infer dates, merge segments, split segments, reorder segments, change timestamps, or change anonymous speaker labels.

## Request Structure

Each provider request contains only the data needed for one contiguous batch:

```json
{
  "meeting_id": "mtg_...",
  "segments": [
    {
      "segment_id": "seg_001",
      "speaker_label": "A",
      "start_seconds": 0.0,
      "end_seconds": 5.2,
      "text": "raw segment text"
    }
  ]
}
```

Speaker labels and timestamps are context only. The local service copies trusted structure from Phase 4.

## Strict Schema

The provider may return only:

```json
{
  "segments": [
    {
      "segment_id": "seg_001",
      "cleaned_text": "Cleaned segment text."
    }
  ]
}
```

The response-format name is:

```text
convointel_transcript_cleanup_batch_v1
```

Extra fields, missing fields, duplicate IDs, missing IDs, extra IDs, changed order, refusal, incomplete responses, malformed JSON, and empty cleaned text for nonempty raw text are rejected.

## Batching

Batches are deterministic and contiguous. A segment is never split. Segment order is preserved, with no overlap, omission, or duplication. Batch size is calculated from a deterministic serialized representation and uses `CONVOINTEL_CLEANUP_MAX_BATCH_CHARACTERS`. A single oversize segment forms its own oversize batch.

## Fidelity Guard

Local checks run before publication:

* segment count, IDs, order, timestamps, and speaker labels must match raw;
* nonempty raw text cannot become empty;
* output expansion is bounded;
* raw segment hashes are computed locally;
* protected-token multisets must match exactly.

Protected tokens include tokens containing digits, dates, times, percentages, currency amounts, URLs, email addresses, version-like identifiers, and alphanumeric identifiers containing digits.

## Cleaned JSON

`transcript/cleaned.json` is UTF-8, deterministic, human-readable JSON with a trailing newline. It contains:

```json
{
  "schema_version": "1.0",
  "meeting_id": "mtg_...",
  "source_raw_transcript_sha256": "...",
  "prompt_version": "convointel-transcript-cleanup-v1",
  "text": "Combined cleaned transcript text",
  "duration_seconds": 42.7,
  "segments": [
    {
      "segment_id": "seg_001",
      "start_seconds": 0.0,
      "end_seconds": 5.2,
      "speaker_label": "A",
      "raw_text_sha256": "...",
      "cleaned_text": "Cleaned segment text.",
      "changed": true
    }
  ]
}
```

No provider-generated timestamps or speaker labels are trusted.

## Cleaned Text

`transcript/cleaned.txt` is rendered deterministically:

```text
[00:00.000-00:05.200] Speaker A: Cleaned segment text.
[00:05.200-00:12.800] Speaker B: Another cleaned segment.
```

Empty transcripts render as one newline.

## Metadata

`metadata/cleanup.json` records provider name, pinned model, schema name, `store=false`, strict-schema usage, prompt version, raw input sizes and hashes, normalization and transcription provenance, cleaned artifact sizes and hashes, changed and unchanged segment counts, batch count, provider request count, and aggregated token usage when available.

It does not persist API keys, response IDs, full provider responses, full request payloads, full prompt text, environment variables, absolute paths, summaries, participant identities, or future intelligence fields.

## Usage Aggregation

For multi-batch cleanup, available token usage is aggregated across successful provider requests. Missing usage remains missing. Cost is not calculated.

## Atomicity And Rollback

Artifacts are written under:

```text
<meeting_dir>/.staging/cleanup_<uuid>/
```

The service publishes `cleaned.json`, `cleaned.txt`, and then `cleanup.json` last. If publication fails, newly published Phase 5 artifacts are removed. Phase 1-4 artifacts are not deleted.

## Idempotency

When all three Phase 5 artifacts exist, the service validates metadata, model, prompt version, schema, `store=false`, raw input hashes, cleaned artifact hashes, segment mappings, changed counts, deterministic cleaned text rendering, combined text, and protected-token fidelity. Valid results are reused without a provider request.

Partial or inconsistent cleanup state raises `CleanupStateError`. Automatic repair and force regeneration are intentionally deferred.

## Validation

Unit tests use `tmp_path`, fake providers, mocked OpenAI clients, synthetic transcripts, and no network calls. Live validation uses one successful Responses API cleanup request against a synthetic complete meeting package.

## Cost Awareness

Phase 5 stores token usage when returned. It does not calculate API cost and does not persist raw provider responses.

## Limitations

Cleanup is conservative and segment-local to the supplied batch. It does not infer speaker identities, normalize dates, resolve participants, or produce meeting intelligence.

## Phase 6 Handoff

Phase 6 may consume the cleaned transcript as a faithful readability layer for general meeting intelligence. Phase 5 does not implement that intelligence.
