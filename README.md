# Convointel

Convointel is a general-purpose meeting-intelligence platform. It is being built backend-first so the local laptop backend is completed and validated before any Android work begins.

## Backend-First Rule

Each phase follows Diagnosis -> Implement -> Validate -> Commit. Do not begin a later phase until the current backend phase has passed validation and been committed.

## Phase 1 Scope

Backend Phase 1 provides a minimal FastAPI backend foundation with typed environment configuration, deterministic path resolution, readable logging, a safe unexpected-error boundary, a versioned health endpoint, automated tests, and local setup documentation.

Deferred features include Android, discovery, pairing, authentication, recording, uploads, transcription, OpenAI integration, meeting intelligence, date extraction, action extraction, gap analysis, calendar integration, PDF generation, databases, dashboards, search, background workers, Docker, cloud deployment, and CI/CD.

## Requirements

Use Windows PowerShell with Python 3.11 or newer.

```powershell
python --version
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

Pytest writes generated temporary and cache files under `.test-artifacts/`, which is ignored by Git. This keeps tests independent of the Windows system temporary directory and does not require global Windows permission changes.

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

Example safe defaults are provided in `.env.example`. Do not commit real `.env` files.

## Legacy reference policy

Curated legacy prototype files, when present, live under `legacy_reference/` as reference-only material. They are not runtime code and do not mean any future capability is implemented.

See:

* [legacy_reference/README.md](legacy_reference/README.md)
* [legacy_reference/reuse-matrix.md](legacy_reference/reuse-matrix.md)
* [legacy_reference/manifest.json](legacy_reference/manifest.json)
