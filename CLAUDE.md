# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Three standalone Python scripts for exporting Fathom meeting transcripts:

1. **`export_fathom_transcripts.py`** — Downloads *all* meetings and their transcripts from the Fathom API (`https://api.fathom.ai/external/v1`) and saves each as a JSON file under `fathom_transcripts/`. Handles pagination (cursor-based) and rate-limiting (429 with exponential backoff).

2. **`convert_fathom_to_md.py`** — Reads the JSON files in `fathom_transcripts/` and converts each to a Markdown file in `fathom_markdown_v2/`. No API calls; purely local conversion. Run after `export_fathom_transcripts.py`.

3. **`export_fathom_transcripts_since.py`** — Combined download + Markdown export in one pass, filtered to meetings on/after a given date. Accepts CLI args.

## Running the scripts

```bash
# Step 1: download all transcripts as JSON
python export_fathom_transcripts.py

# Step 2: convert downloaded JSONs to Markdown
python convert_fathom_to_md.py

# Or: download + export since a date (combined, no separate JSON step)
python export_fathom_transcripts_since.py --since 2026-01-01 --outdir ./fathom_md

# Dry run (list meetings without downloading)
python export_fathom_transcripts_since.py --since 2026-01-01 --dry-run
```

All timestamps are UTC. Dependencies: `requests` (stdlib only otherwise, Python 3.9+). Install with:
```bash
pip install requests
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
