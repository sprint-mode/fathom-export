#!/usr/bin/env python3
"""
Download Fathom meetings (from a given date) and export each meeting to Markdown.

Filename scheme (as previously defined):
  "YYYY-MM-DD HHmm - <title> - <calendar invitees>.md"

Also embeds full meeting metadata at the top of the .md.

Notes:
- I do NOT know your exact Fathom API base URL/endpoints/auth format from this chat.
- This script is written to be *drop-in adaptable*:
  - Set FATHOM_BASE_URL
  - Set LIST_MEETINGS_PATH and GET_MEETING_PATH/GET_TRANSCRIPT_PATH as needed
  - Adjust auth header in `auth_headers()`

Usage:
  python export_fathom_transcripts_since.py --since 2026-02-01 --outdir ./fathom_md

If you already know the exact endpoints, you'll only need to edit the constants near the top.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

_config = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))


# =========================
# CONFIGURE THESE
# =========================

# TODO: set to the real Fathom API base URL you use in your existing scripts.
#FATHOM_BASE_URL = "https://api.fathom.example.com"
FATHOM_BASE_URL = "https://api.fathom.ai/external"

# TODO: set these to the real endpoints.
# Expected behavior:
# - LIST_MEETINGS_PATH should return a list of meetings with at least:
#     id, title, start_time (or created_at), url, attendees/invitees
# - GET_MEETING_PATH should return full meeting metadata
# - GET_TRANSCRIPT_PATH should return transcript text + optionally speakers/timestamps
LIST_MEETINGS_PATH = "/v1/meetings"
GET_TRANSCRIPT_PATH = "/v1/recordings/{meeting_id}/transcript"

# TODO: adjust if your API uses a different auth header.
# Common patterns:
#   Authorization: Bearer <token>
#   X-API-Key: <token>
AUTH_MODE = "x-api-key"  # "bearer" or "x-api-key"
API_KEY_ENV = "FATHOM_API_KEY"

# Pagination knobs (adjust to your API)
PAGE_PARAM = "page"
PAGE_SIZE_PARAM = "limit"
PAGE_SIZE = 50

# If the invitees list is too long, keep first N and append "+K"
MAX_INVITEES_IN_FILENAME = 8

# Limit filename length (Windows-safe-ish)
MAX_FILENAME_CHARS = 180

# Backoff
RETRY_COUNT = 5
RETRY_BACKOFF_SECONDS = 1.5


# =========================
# UTILITIES
# =========================

def auth_headers() -> Dict[str, str]:
    api_key = _config.get("fathom_api_key")
    if not api_key:
        raise SystemExit(
            "Missing API key. Set 'fathom_api_key' in config.json.\n"
            "Copy config.example.json to config.json and fill in your key.\n"
        )

    if AUTH_MODE == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if AUTH_MODE == "x-api-key":
        return {"X-API-Key": api_key}
    raise SystemExit(f"Unknown AUTH_MODE: {AUTH_MODE}")


def request_json(method: str, url: str, params: Optional[dict] = None) -> Any:
    headers = {"Accept": "application/json", **auth_headers()}
    last_err = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.request(method, url, headers=headers, params=params, timeout=30)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        except Exception as e:
            last_err = e
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            else:
                raise RuntimeError(f"Request failed after {RETRY_COUNT} attempts: {url}\n{last_err}") from last_err


def parse_date(s: str) -> dt.date:
    # YYYY-MM-DD
    return dt.date.fromisoformat(s)


def sanitize_filename_part(s: str) -> str:
    s = s.strip()
    # Remove filesystem-dangerous characters
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Avoid trailing dots/spaces (Windows)
    s = s.rstrip(". ").strip()
    return s


def truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 2
    while True:
        candidate = parent / f"{stem} - meeting {n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def iso_to_dt(value: str) -> dt.datetime:
    """
    Parse common ISO timestamps.
    Accepts 'Z' suffix.
    """
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return dt.datetime.fromisoformat(v)


def format_dt_for_filename(d: dt.datetime):
    return d.strftime("%Y-%m-%d"), d.strftime("%H%M")


def format_dt_for_md(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S UTC")


def pick_invitees_for_filename(invitees: List[str]) -> str:
    invitees = [sanitize_filename_part(x) for x in invitees if x and x.strip()]
    invitees = [x for x in invitees if x]
    if not invitees:
        return "No invitees"
    if len(invitees) <= MAX_INVITEES_IN_FILENAME:
        return ", ".join(invitees)
    keep = invitees[:MAX_INVITEES_IN_FILENAME]
    return ", ".join(keep) + f", +{len(invitees) - len(keep)}"


def md_escape(s: str) -> str:
    # Minimal escaping for YAML-ish header; keep it simple.
    return s.replace("\n", " ").strip()


# =========================
# DATA SHAPES (expected)
# =========================

@dataclass
class Meeting:
    id: str
    title: str
    url: Optional[str]
    start_time: dt.datetime
    created_at: Optional[dt.datetime]
    invitees: List[str]
    raw: Dict[str, Any]


def extract_meeting_fields(item: Dict[str, Any]) -> Meeting:
    """
    Map your API response into our Meeting dataclass.

    TODO: You may need to adjust key names here based on your real API response.
    Common keys:
      - id
      - title / name
      - url / share_url
      - start_time / started_at / meeting_start
      - created_at
      - attendees / invitees / participants
    """
    meeting_id = str(item.get("id") or item.get("meeting_id") or item.get("recording_id") or "")
    if not meeting_id:
        raise ValueError(f"Meeting missing id field: {item.keys()}")

    title = str(item.get("title") or item.get("name") or "Untitled meeting")

    url = item.get("url") or item.get("share_url") or item.get("meeting_url")

    # Prefer start_time; fallback to created_at
    start_raw = (item.get("start_time") or item.get("started_at") or item.get("meeting_start")
                 or item.get("recording_start_time") or item.get("scheduled_start_time"))
    created_raw = item.get("created_at") or item.get("createdAt")

    if start_raw:
        start_dt = iso_to_dt(str(start_raw))
    elif created_raw:
        start_dt = iso_to_dt(str(created_raw))
    else:
        raise ValueError(f"Meeting {meeting_id} missing start_time/created_at")

    created_dt = iso_to_dt(str(created_raw)) if created_raw else None

    invitees: List[str] = []
    attendees = item.get("calendar_invitees") or item.get("invitees") or item.get("attendees") or item.get("participants") or []
    if isinstance(attendees, list):
        for a in attendees:
            if isinstance(a, str):
                invitees.append(a)
            elif isinstance(a, dict):
                # Try common fields: name/email
                invitees.append(a.get("name") or a.get("email") or "")
    # Remove blanks
    invitees = [x for x in invitees if x]

    return Meeting(
        id=meeting_id,
        title=title,
        url=str(url) if url else None,
        start_time=start_dt,
        created_at=created_dt,
        invitees=invitees,
        raw=item,
    )


# =========================
# API OPERATIONS
# =========================

def list_meetings_since(since_date: dt.date) -> List[Dict[str, Any]]:
    """
    Fetch all meetings since since_date (inclusive).
    TODO: adjust query params to match your API:
      - some APIs use `from` / `after` / `start_date`
      - some require pagination tokens instead of pages
    """
    results: List[Dict[str, Any]] = []
    page = 1

    while True:
        url = f"{FATHOM_BASE_URL}{LIST_MEETINGS_PATH}"
        params = {
            # TODO: adjust param name to your API:
            "since": since_date.isoformat(),
            PAGE_PARAM: page,
            PAGE_SIZE_PARAM: PAGE_SIZE,
        }
        data = request_json("GET", url, params=params)

        # TODO: adjust unpacking:
        # Expected:
        #   data = { "items": [...], "has_more": bool } OR list directly
        if isinstance(data, list):
            items = data
            has_more = len(items) == PAGE_SIZE
        else:
            items = data.get("items") or data.get("data") or data.get("results") or []
            has_more = bool(data.get("has_more") or data.get("hasMore") or data.get("next_page"))

        if not items:
            break

        results.extend(items)

        if not has_more:
            break

        page += 1

    return results


def get_meeting_transcript(meeting_id: str) -> Dict[str, Any]:
    url = f"{FATHOM_BASE_URL}{GET_TRANSCRIPT_PATH.format(meeting_id=meeting_id)}"
    return request_json("GET", url)


# =========================
# EXPORT
# =========================

def extract_speakers_from_transcript(transcript_payload: Dict[str, Any]) -> List[str]:
    """Return unique speaker names found in the transcript blocks."""
    speakers: List[str] = []

    def collect_from_blocks(blocks):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            sp = block.get("speaker")
            if isinstance(sp, dict):
                name = sp.get("display_name") or sp.get("name") or sp.get("email") or ""
                name = name.strip()
                if name and name not in speakers:
                    speakers.append(name)

    if not isinstance(transcript_payload, dict):
        return speakers

    raw_transcript = transcript_payload.get("transcript")
    if isinstance(raw_transcript, list):
        collect_from_blocks(raw_transcript)

    segments = transcript_payload.get("segments") or transcript_payload.get("utterances") or []
    if isinstance(segments, list):
        collect_from_blocks(segments)

    return speakers


def build_filename(meeting: Meeting, transcript_speakers: Optional[List[str]] = None) -> str:
    date_part, time_part = format_dt_for_filename(meeting.start_time)
    title = sanitize_filename_part(meeting.title)

    # Merge calendar invitees + transcript speakers, preserving order, no duplicates
    combined: List[str] = list(meeting.invitees)
    for sp in (transcript_speakers or []):
        if sp not in combined:
            combined.append(sp)

    invitees = pick_invitees_for_filename(combined)

    # Compose and truncate
    base = f"{date_part} {time_part} - {title} - {invitees}"
    base = truncate(base, MAX_FILENAME_CHARS)
    return base + ".md"


def render_markdown(meeting: Meeting, transcript_payload: Dict[str, Any]) -> str:
    """
    Creates an .md file that includes:
      - A metadata header (YAML-like)
      - A transcript section (best-effort mapping)
    TODO: adjust transcript extraction to your API response structure.
    """
    created_str = format_dt_for_md(meeting.created_at) if meeting.created_at else ""
    start_str = format_dt_for_md(meeting.start_time)

    # Transcript extraction
    transcript_text = ""
    speakers_block = ""

    def fmt_ts(seconds):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return ""
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def render_blocks(blocks):
        lines = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            txt = (block.get("text") or "").strip()
            if not txt:
                continue
            sp = block.get("speaker")
            spk = (sp.get("display_name") or sp.get("name") or sp.get("email") or "Unknown") if isinstance(sp, dict) else "Unknown"
            ts = fmt_ts(block.get("start_seconds") or block.get("start_time_seconds") or block.get("start"))
            lines.append(f"- **[{ts}] {spk}:** {txt}" if ts else f"- **{spk}:** {txt}")
        return "\n".join(lines)

    if isinstance(transcript_payload, dict):
        raw_transcript = transcript_payload.get("transcript")

        if isinstance(raw_transcript, list):
            speakers_block = render_blocks(raw_transcript)
        elif isinstance(raw_transcript, str):
            transcript_text = raw_transcript
        else:
            transcript_text = (transcript_payload.get("text") or transcript_payload.get("content") or "")

        if not speakers_block:
            segments = transcript_payload.get("segments") or transcript_payload.get("utterances") or []
            if isinstance(segments, list):
                speakers_block = render_blocks(segments)

    transcript_section = speakers_block if speakers_block else transcript_text

    header = [
        "---",
        "source: fathom",
        f"meeting_id: {md_escape(meeting.id)}",
        f"title: {md_escape(meeting.title)}",
        f"url: {md_escape(meeting.url or '')}",
        f"start_time: {md_escape(start_str)}",
        f"created_at: {md_escape(created_str)}",
        f"invitees: {json.dumps(meeting.invitees, ensure_ascii=False)}",
        "---",
        "",
        f"# {meeting.title}",
        "",
        "## Metadata",
        f"- **Start:** {start_str}",
        f"- **Created:** {created_str}" if created_str else "- **Created:**",
        f"- **URL:** {meeting.url or ''}",
        f"- **Invitees:** {', '.join(meeting.invitees) if meeting.invitees else ''}",
        "",
        "## Transcript",
        transcript_section.strip() if transcript_section else "",
        "",
        "## Raw Payload (Meeting)",
        "```json",
        json.dumps(meeting.raw, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Raw Payload (Transcript)",
        "```json",
        json.dumps(transcript_payload, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(header)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, help="Start date (YYYY-MM-DD), inclusive")
    parser.add_argument("--outdir", default="./transcripts", help="Output folder")
    parser.add_argument("--dry-run", action="store_true", help="Do not download/export, just list meetings")
    args = parser.parse_args()

    since_date = dt.date.fromisoformat(args.since)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    items = list_meetings_since(since_date)

    meetings: List[Meeting] = []
    for item in items:
        try:
            meetings.append(extract_meeting_fields(item))
        except Exception as e:
            print(f"Skipping item (cannot parse): {e}")
            continue

    meetings = [m for m in meetings if m.start_time.date() >= since_date]
    meetings.sort(key=lambda m: m.start_time)

    print(f"Found {len(meetings)} meetings since {since_date.isoformat()} (UTC).")

    if args.dry_run:
        for m in meetings:
            print(f"- {format_dt_for_md(m.start_time)} | {m.id} | {m.title}")
        return

    for m in meetings:
        try:
            transcript = get_meeting_transcript(m.id)
        except Exception as e:
            print(f"[{m.id}] Error: could not fetch transcript: {e}")
            transcript = {}

        speakers = extract_speakers_from_transcript(transcript)
        filename = build_filename(m, speakers)
        path = outdir / filename
        if path.exists():
            print(f"[{m.id}] SKIP (already exists): {filename}")
            continue

        path = ensure_unique_path(path)

        md = render_markdown(m, transcript)
        path.write_text(md, encoding="utf-8")

        print(f"[{m.id}] Saved: {path}")

    print("Done.")


if __name__ == "__main__":
    main()
