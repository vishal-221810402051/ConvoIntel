# Phase 01 - Audio Intake

## Objective
Implement the raw audio intake service for `.m4a`, `.mp3`, and `.wav`.

## Scope
- Validate source file
- Validate extension
- Generate meeting ID
- Create meeting package root
- Copy original audio
- Write `metadata/intake.json`

## Out of Scope
- Normalization
- Transcription
- OpenAI integration
- Database persistence

## Storage Structure
`data/processed/<meeting_id>/`
- `source/original.<ext>`
- `metadata/intake.json`
- `logs/`

## Validation
Use:
- one valid `.m4a`
- one valid `.mp3`
- one valid `.wav`
- one invalid file

## Pass Criteria
- valid files accepted
- invalid file rejected
- intake metadata written
- meeting folder created
