# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Three Python scripts for exporting Fathom meeting transcripts to Markdown and syncing them to Google Drive:

1. **`export_fathom_transcripts_since.py`** — Downloads meetings and transcripts from the Fathom API (`https://api.fathom.ai/external/v1`) since a given date and exports each directly to a Markdown file in `transcripts/`. Handles pagination (cursor-based) and rate-limiting (429 with exponential backoff).

2. **`upload_to_drive.py`** — Uploads Markdown files from `transcripts/` to a Google Drive folder. Skips files already present. Accepts `--since` to filter by date.

3. **`sync.py`** — Orchestrates a full sync: checks the latest transcript date in Drive, exports new meetings since that date, then uploads them.

## Running the scripts

```bash
# Full sync (recommended): export new transcripts and upload to Drive
python sync.py

# First run or backfill from a specific date
python sync.py --since 2026-01-01

# Export only (no Drive upload)
python export_fathom_transcripts_since.py --since 2026-01-01

# Dry run (list meetings without downloading)
python export_fathom_transcripts_since.py --since 2026-01-01 --dry-run

# Upload only
python upload_to_drive.py --since 2026-01-01
```

All timestamps are UTC. Dependencies:
```bash
pip install requests google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

## API key / config

Both scripts load `config.json` from the working directory at startup:

```json
{ "fathom_api_key": "YOUR_KEY" }
```

Copy `config.example.json` → `config.json` and fill in your key. `config.json` is gitignored.

## Key data flow

- **API endpoints used:** `GET /meetings` (paginated, cursor-based), `GET /recordings/{recording_id}/transcript`
- **JSON structure saved:** `{ "meeting": <meeting object>, "transcript": <transcript list or object> }`
- **Markdown filename format:** `YYYY-MM-DD HHmm - <title> - <invitees>.md`
- **Duplicate filenames** are resolved by appending ` - meeting N` to the stem.

## JSON schema flexibility

Both the converter and `_since` script defensively probe multiple key names (e.g., `meeting_title` vs `title`, `calendar_invitees` vs `attendees` vs `participants`, `start_seconds` vs `start_time_seconds` vs `start`) because Fathom's API response shape can vary. When adding new field extraction, follow this `coalesce()`/multi-key pattern rather than assuming a single key.
