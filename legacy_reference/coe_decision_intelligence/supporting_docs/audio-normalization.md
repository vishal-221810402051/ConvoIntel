# Phase 02 - Audio Normalization

## Objective
Normalize Phase 01 source audio into a transcription-ready WAV format.

## Input
`data/processed/<meeting_id>/source/original.<ext>`

## Output
- `data/processed/<meeting_id>/normalized/audio.wav`
- `data/processed/<meeting_id>/metadata/normalization.json`

## Normalization Spec
- mono (`-ac 1`)
- 16000 Hz (`-ar 16000`)
- PCM 16-bit (`-c:a pcm_s16le`)

## Preconditions
- meeting directory exists
- source directory exists
- exactly one `original.*` file exists
- source extension is one of `.m4a`, `.mp3`, `.wav`

## Overwrite Policy
`replace_existing_output`

Existing `normalized/audio.wav` is replaced using FFmpeg `-y`.

## Failure Handling
- missing meeting folder: `FileNotFoundError`
- missing source dir/file: `FileNotFoundError`
- unsupported extension: `ValueError`
- multiple `original.*` files: `ValueError`
- FFmpeg missing: `RuntimeError`
- FFmpeg conversion failure: `RuntimeError` with stderr summary
- partial output cleanup on FFmpeg failure
- no `normalization_completed` metadata written on failure

## Validation Commands
```powershell
python scripts/test_audio_normalization.py "<meeting_id>"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,sample_rate,channels -of default=nw=1:nk=1 "data/processed/<meeting_id>/normalized/audio.wav"
Get-Content "data/processed/<meeting_id>/metadata/normalization.json"
```
