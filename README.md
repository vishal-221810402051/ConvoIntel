# Convointel

Convointel is a general-purpose meeting-intelligence platform. It is being built backend-first so the local laptop backend is completed and validated before any Android work begins.

## Backend-First Rule

Each phase follows Diagnosis -> Implement -> Validate -> Commit. Do not begin a later phase until the current backend phase has passed validation and been committed.

## Phase 1 Scope

Backend Phase 1 provides a minimal FastAPI backend foundation with typed environment configuration, deterministic path resolution, readable logging, a safe unexpected-error boundary, a versioned health endpoint, automated tests, and local setup documentation.

Deferred features include Android, discovery, pairing, authentication, recording, uploads, transcription, OpenAI integration, meeting intelligence, date extraction, action extraction, gap analysis, calendar integration, PDF generation, databases, dashboards, search, background workers, Docker, cloud deployment, and CI/CD.

## Phase 2 Capability

Backend Phase 2 adds local source-audio intake into a canonical meeting package for `.m4a`, `.mp3`, and `.wav` files. It preserves original bytes and writes typed package metadata, but it does not add uploads, normalization, transcription, intelligence, or Android sync.

See [docs/phase-02-audio-intake.md](docs/phase-02-audio-intake.md).

## Phase 3 Capability

Backend Phase 3 adds local audio normalization for completed meeting packages. It verifies the Phase 2 manifest and source checksum, converts the preserved source audio to canonical mono 16 kHz PCM S16LE WAV with FFmpeg, validates the output with FFprobe, and writes separate normalization metadata. It does not add transcription, intelligence, uploads, or Android sync.

See [docs/phase-03-audio-normalization.md](docs/phase-03-audio-normalization.md).

## Phase 4 Capability

Backend Phase 4 adds provider-isolated diarized raw transcription for normalized meeting packages. It verifies Phase 3 metadata and audio integrity, sends only `normalized/audio.wav` to the configured OpenAI transcription provider, and writes canonical raw transcript artifacts plus transcription metadata. It does not add cleanup, summaries, intelligence extraction, reporting, uploads, or Android sync.

See [docs/phase-04-diarized-transcription.md](docs/phase-04-diarized-transcription.md).

## Phase 5 Capability

Backend Phase 5 adds provider-isolated transcript cleanup for completed Phase 4 packages. It verifies raw transcript artifacts and transcription metadata before any provider call, sends transcript segments to the OpenAI Responses API as untrusted data, and writes canonical cleaned transcript artifacts plus cleanup metadata. It preserves segment IDs, timestamps, anonymous speaker labels, language, and protected tokens. It does not add summaries, decisions, action items, owners, risks, calendar logic, reports, endpoints, databases, uploads, or Android sync.

See [docs/phase-05-transcript-cleanup.md](docs/phase-05-transcript-cleanup.md).

## Phase 6 Capability

Backend Phase 6 adds provider-isolated general decision intelligence for completed Phase 5 packages. It verifies the full Phase 1-5 artifact chain, sends only the canonical cleaned transcript to the OpenAI Responses API as untrusted data, and writes evidence-grounded structured intelligence plus metadata. It extracts general meeting categories such as decisions, actions, commitments, risks, blockers, dependencies, unresolved questions, missing information, recommendations, and locally derived gaps. It does not add temporal normalization, calendar sync, reports, endpoints, databases, Android sync, search, or mission-specific profiles.

See [docs/phase-06-general-decision-intelligence.md](docs/phase-06-general-decision-intelligence.md).

## Phase 7 Capability

Backend Phase 7 adds provider-isolated temporal intelligence for completed Phase 6 packages. It validates the full Phase 1-6 artifact chain, accepts an optional explicit runtime meeting reference datetime plus IANA timezone, sends only cleaned transcript segments and limited temporal Phase 6 context to the OpenAI Responses API as untrusted data, and writes canonical temporal intelligence plus metadata. It extracts grounded temporal expressions, normalizes supported dates, times, ranges, durations, recurrences, reminders-as-data, and locally derived gaps without using intake time as meeting time. It does not add calendar sync, event creation, scheduling, notifications, reports, endpoints, databases, Android sync, search, or mission-specific profiles.

See [docs/phase-07-temporal-intelligence.md](docs/phase-07-temporal-intelligence.md).

## Requirements

Use Windows PowerShell with Python 3.11 or newer. Phase 3 runtime validation also requires FFmpeg and FFprobe executables on PATH. Phase 4, Phase 5, and Phase 6 live validation require a usable OpenAI API key in `OPENAI_API_KEY` or `CONVOINTEL_OPENAI_API_KEY`.

```powershell
python --version
ffmpeg -version
ffprobe -version
```

## Setup

Run these commands from the repository root.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation scripts, use this troubleshooting command only if your local policy allows it:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Start The Backend

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

## Health Check

From another PowerShell terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health
```

Expected logical response:

```text
status      : ok
service     : convointel-backend
api_version : v1
```

## Run Tests

```powershell
python -m pytest
```

Pytest's nonessential cache provider is disabled for portability. Temporary files use the normal pytest temporary directory behavior.

For strict warning validation, run:

```powershell
python -m pytest -W error -ra
```

## Configuration

Convointel reads these environment variables:

| Variable | Default |
| --- | --- |
| `CONVOINTEL_ENV` | `development` |
| `CONVOINTEL_HOST` | `127.0.0.1` |
| `CONVOINTEL_PORT` | `8765` |
| `CONVOINTEL_LOG_LEVEL` | `INFO` |
| `CONVOINTEL_DATA_DIR` | `<repository-root>/data` |
| `CONVOINTEL_FFMPEG_BINARY` | `ffmpeg` |
| `CONVOINTEL_FFPROBE_BINARY` | `ffprobe` |
| `CONVOINTEL_NORMALIZATION_TIMEOUT_SECONDS` | `1800` |
| `OPENAI_API_KEY` | unset |
| `CONVOINTEL_OPENAI_API_KEY` | unset |
| `CONVOINTEL_TRANSCRIPTION_MODEL` | `gpt-4o-transcribe-diarize` |
| `CONVOINTEL_TRANSCRIPTION_TIMEOUT_SECONDS` | `1800` |
| `CONVOINTEL_TRANSCRIPTION_MAX_RETRIES` | `2` |
| `CONVOINTEL_TRANSCRIPTION_LANGUAGE` | unset |
| `CONVOINTEL_CLEANUP_MODEL` | `gpt-5-mini-2025-08-07` |
| `CONVOINTEL_CLEANUP_TIMEOUT_SECONDS` | `900` |
| `CONVOINTEL_CLEANUP_MAX_RETRIES` | `2` |
| `CONVOINTEL_CLEANUP_MAX_BATCH_CHARACTERS` | `50000` |
| `CONVOINTEL_CLEANUP_MAX_OUTPUT_TOKENS` | `16000` |
| `CONVOINTEL_INTELLIGENCE_MODEL` | `gpt-5-mini-2025-08-07` |
| `CONVOINTEL_INTELLIGENCE_TIMEOUT_SECONDS` | `1200` |
| `CONVOINTEL_INTELLIGENCE_MAX_RETRIES` | `2` |
| `CONVOINTEL_INTELLIGENCE_MAX_INPUT_CHARACTERS` | `500000` |
| `CONVOINTEL_INTELLIGENCE_MAX_OUTPUT_TOKENS` | `32000` |
| `CONVOINTEL_INTELLIGENCE_MAX_ITEMS_PER_CATEGORY` | `100` |
| `CONVOINTEL_TEMPORAL_MODEL` | `gpt-5-mini-2025-08-07` |
| `CONVOINTEL_TEMPORAL_TIMEOUT_SECONDS` | `1200` |
| `CONVOINTEL_TEMPORAL_MAX_RETRIES` | `2` |
| `CONVOINTEL_TEMPORAL_MAX_INPUT_CHARACTERS` | `600000` |
| `CONVOINTEL_TEMPORAL_MAX_OUTPUT_TOKENS` | `24000` |
| `CONVOINTEL_TEMPORAL_MAX_ITEMS` | `300` |

Example safe defaults are provided in `.env.example`. Do not commit real `.env` files.

## Legacy reference policy

Curated legacy prototype files, when present, live under `legacy_reference/` as reference-only material. They are not runtime code and do not mean any future capability is implemented.

See:

* [legacy_reference/README.md](legacy_reference/README.md)
* [legacy_reference/reuse-matrix.md](legacy_reference/reuse-matrix.md)
* [legacy_reference/manifest.json](legacy_reference/manifest.json)
