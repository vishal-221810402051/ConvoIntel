# Backend Phase 1: Backend Foundation and Architecture Scaffold

## Objective

Backend Phase 1 creates the minimal Python backend foundation for Convointel. The backend starts locally, exposes a deterministic versioned health endpoint, loads typed environment configuration, resolves paths from the repository location, initializes readable logging, provides a safe unexpected-error boundary, and includes automated tests.

## Scope

This phase includes only the repository scaffold, FastAPI application entrypoint, API v1 router, health endpoint, typed settings, deterministic paths, centralized logging, minimal exception handling, dependency metadata, tests, `.env.example`, `.gitignore`, the root README, and this architecture note.

## Exclusions

This phase does not include Android, Android Studio, NSD, mDNS, Zeroconf, automatic backend discovery, device pairing, authentication, audio recording, upload endpoints, multipart upload, resumable upload, FFmpeg, audio normalization, transcription, OpenAI integration, transcript cleanup, summarization, decision extraction, action extraction, date extraction, gap analysis, calendar integration, PDF generation, database business schemas, Streamlit, dashboard UI, search, mission memory, background workers, Celery, Redis, Docker, cloud deployment, or CI/CD.

## Architecture

`backend.app.main` owns the FastAPI application factory and the importable `app` target used by Uvicorn.

`backend.app.api.router` assembles versioned API routers under `/api`.

`backend.app.api.v1.health` defines the typed health response and `GET /api/v1/health`.

`backend.app.config` defines application identity constants and typed `CONVOINTEL_*` settings.

`backend.app.core.paths` resolves the repository root and data directory without depending on `Path.cwd()`.

`backend.app.logging_config` configures timestamped application logging without duplicate Convointel handlers.

`backend.app.core.exceptions` registers the minimal unexpected-exception boundary.

`backend.tests` contains Phase 1 tests for application import, health contract, settings, paths, validation failures, and logging handler duplication.

## Configuration Variables

| Variable | Default | Description |
| --- | --- | --- |
| `CONVOINTEL_ENV` | `development` | Runtime environment label. |
| `CONVOINTEL_HOST` | `127.0.0.1` | Host used for local Uvicorn startup. |
| `CONVOINTEL_PORT` | `8765` | Port used for local Uvicorn startup. |
| `CONVOINTEL_LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `CONVOINTEL_DATA_DIR` | `<repository-root>/data` | Local data directory, normalized to an absolute path. |

## API Contract

Endpoint:

```text
GET /api/v1/health
```

Expected status:

```text
200
```

Expected JSON body:

```json
{
  "status": "ok",
  "service": "convointel-backend",
  "api_version": "v1"
}
```

## Validation Commands

Run these commands from the repository root in PowerShell.

```powershell
git branch --show-current
git remote -v
git status --short
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -c "from backend.app.main import app; print(app.title)"
python -c "from backend.app.config import get_settings; s=get_settings(); print(s.host, s.port, s.data_dir)"
python -m pytest
python -m pytest -W error -ra
python -m compileall backend
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

From another PowerShell terminal while Uvicorn is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/health
```

Configuration override check:

```powershell
$env:CONVOINTEL_PORT = "8877"
python -c "from backend.app.config import get_settings; get_settings.cache_clear(); print(get_settings().port)"
Remove-Item Env:CONVOINTEL_PORT
```

Git quality checks:

```powershell
git diff --check
git status --short
```

If PowerShell blocks virtual-environment activation, run PowerShell as the current user and use this troubleshooting command only if your local policy allows it:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Expected Outputs

`git branch --show-current` prints `main`.

`git remote -v` shows `origin` pointing to `https://github.com/vishal-221810402051/ConvoIntel.git` for fetch and push.

The import check prints `Convointel Backend`.

The configuration check prints host `127.0.0.1`, port `8765`, and an absolute path ending in `data`.

The health request returns `status: ok`, `service: convointel-backend`, and `api_version: v1`.

`python -m pytest` passes all Phase 1 tests.

`python -m pytest -W error -ra` passes all Phase 1 tests with warnings treated as errors.

`python -m compileall backend` reports no syntax errors.

`git diff --check` reports no whitespace errors for tracked changes. For this initial uncommitted repository, an additional source whitespace inspection should be run because all files are untracked until the first commit.

Pytest writes generated temporary and cache content to `.test-artifacts/tmp` and `.test-artifacts/cache`. The `.test-artifacts/` directory is ignored by Git, and no global Windows temporary-directory permissions need to be changed for Phase 1 validation.

## Pass/Fail Criteria

Phase 1 passes when the local Git repository is initialized on `main`, `origin` points to the official Convointel repository, the application imports, Uvicorn starts, the health endpoint returns the exact contract, typed configuration and environment overrides work, paths are absolute and deterministic, logging avoids duplicate Convointel handlers, tests pass, no secrets are tracked, and no future-phase functionality is present.

## Known Phase 1 Limitations

The backend has no persistence beyond reserving the local `data` directory. It does not process meetings, accept uploads, call external services, authenticate users, generate reports, or provide a user interface.
