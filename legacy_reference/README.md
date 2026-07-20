# Legacy Reference Assets

This directory contains quarantined engineering references copied from the legacy CoE Decision Intelligence prototype.

Source repository: `https://github.com/vishal-221810402051/CoE-Decision-Intelligence.git`
Source branch: `main`
Source commit: `27187565b5b6c10581c770c7b4c0b4d79a30f13b`
Policy: `reference_only`

These files are not active Convointel runtime code. Their presence does not mean audio intake, normalization, transcription, intelligence extraction, calendar recommendations, reporting, Android support, or any other future capability has been implemented.

Future phases must port behavior deliberately through the appropriate gated Convointel architecture. CoE-specific logic may only return as an optional domain profile, never as implicit core behavior. Android assets are deferred until the backend is complete and the Android phase is explicitly opened.

Do not import files from `legacy_reference/` into `backend/`. Do not add `__init__.py` files under this directory. Do not add this directory to package discovery, `pythonpath`, or active pytest discovery.

## Updating The Reference Set

1. Verify the active Convointel branch is clean and synchronized.
2. Fetch the legacy repository through the separate `legacy` remote without merging histories.
3. Copy only approved files from Git objects such as `legacy/main:<path>`.
4. Keep copied files quarantined under `legacy_reference/`.
5. Update `manifest.json` with source blob SHAs and destination SHA-256 hashes.
6. Update `reuse-matrix.md` with classification and rewrite notes.
7. Run the active backend tests and packaging-isolation checks before committing.
