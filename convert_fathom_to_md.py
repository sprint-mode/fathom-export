import os
import json
import re
from pathlib import Path
from datetime import datetime, timezone

INPUT_DIR = Path("fathom_transcripts")          # your downloaded JSONs (one per meeting)
OUTPUT_DIR = Path("fathom_markdown_v2")         # output folder
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILENAME_LEN = 220  # conservative for Windows paths

# ---------- helpers ----------

def safe_filename(text: str) -> str:
    text = (text or "").strip()
    # Windows-illegal chars
    text = re.sub(r'[<>:"/\\|?*]+', "_", text)
    # normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text

def parse_iso_datetime(s: str):
    if not s:
        return None
    try:
        # handle trailing Z
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def ymd_hm_from_dt(dt: datetime):
    if not dt:
        return None, None
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H%M")

def coalesce(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return None

def get_meeting_obj(data: dict) -> dict:
    # supports either {"meeting": {...}, "transcript": ...} or raw meeting payloads
    if isinstance(data, dict) and isinstance(data.get("meeting"), dict):
        return data["meeting"]
    return data if isinstance(data, dict) else {}

def get_transcript_list(data):
    """
    supports:
      - {"meeting": {...}, "transcript": [...]}
      - {"transcript": [...]}
      - transcript payload variations
    """
    if isinstance(data, dict):
        t = data.get("transcript")
        if isinstance(t, list):
            return t
        # sometimes nested
        if isinstance(t, dict) and isinstance(t.get("transcript"), list):
            return t["transcript"]
    return []

def normalize_person(p):
    """
    Accepts dict or str.
    Returns display string for invitees list: prefer display_name/name, else email, else id.
    """
    if isinstance(p, str):
        return p.strip()
    if isinstance(p, dict):
        return coalesce(
            p.get("display_name"),
            p.get("name"),
            p.get("full_name"),
            p.get("email"),
            p.get("user_email"),
            p.get("id"),
        )
    return None

def extract_invitees(meeting: dict):
    """
    "calendar invitees" can be stored under different keys depending on payload.
    We'll try a few common ones. You can add keys if your JSON shows a different structure.
    """
    candidate_keys = [
        "calendar_invitees",
        "invitees",
        "attendees",
        "participants",
        "meeting_attendees",
    ]

    people = []
    for k in candidate_keys:
        v = meeting.get(k)
        if isinstance(v, list):
            for item in v:
                s = normalize_person(item)
                if s:
                    people.append(s)

    # De-dup while keeping order
    seen = set()
    uniq = []
    for s in people:
        key = s.lower()
        if key not in seen:
            uniq.append(s)
            seen.add(key)

    return uniq

def extract_url(meeting: dict):
    return coalesce(
        meeting.get("url"),
        meeting.get("meeting_url"),
        meeting.get("share_url"),
        meeting.get("fathom_url"),
        meeting.get("recording_url"),
        meeting.get("web_url"),
    )

def speaker_name(block: dict) -> str:
    sp = block.get("speaker")
    if isinstance(sp, dict):
        return coalesce(sp.get("display_name"), sp.get("name"), sp.get("email"), "Unknown") or "Unknown"
    return "Unknown"

def fmt_ts(seconds):
    if seconds is None:
        return ""
    try:
        seconds = float(seconds)
    except Exception:
        return ""
    m = int(seconds // 60)
    s = int(seconds % 60)
    h = int(m // 60)
    m = int(m % 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def ensure_unique_path(base_path: Path) -> Path:
    if not base_path.exists():
        return base_path
    stem, suffix, parent = base_path.stem, base_path.suffix, base_path.parent
    n = 2
    while True:
        candidate = parent / f"{stem} - meeting {n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1

def shorten_invitees(invitees):
    """
    Filenames can explode. Keep it readable:
      - if <= 5 invitees: list all
      - else: first 5 + "+N"
    """
    if not invitees:
        return ""
    if len(invitees) <= 5:
        return ", ".join(invitees)
    return ", ".join(invitees[:5]) + f" +{len(invitees) - 5}"

def markdown_kv(d: dict, key: str, value):
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, (dict, list)):
        value_str = json.dumps(value, ensure_ascii=False)
    else:
        value_str = str(value)
    return f"- **{key}:** {value_str}\n"

# ---------- main ----------

converted = 0

for json_file in INPUT_DIR.glob("*.json"):
    with json_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    meeting = get_meeting_obj(data)
    transcript = get_transcript_list(data)

    title = coalesce(meeting.get("meeting_title"), meeting.get("title"), meeting.get("name"), json_file.stem) or json_file.stem

    created_at_raw = coalesce(meeting.get("created_at"), meeting.get("start_time"), meeting.get("started_at"), meeting.get("meeting_started_at"))
    dt = parse_iso_datetime(created_at_raw) if isinstance(created_at_raw, str) else None

    # fallback: file mtime if no datetime in metadata
    if not dt:
        dt = datetime.fromtimestamp(json_file.stat().st_mtime, tz=timezone.utc)

    date_ymd, time_hm = ymd_hm_from_dt(dt)

    invitees = extract_invitees(meeting)
    invitees_for_name = shorten_invitees(invitees)
    invitees_for_name = safe_filename(invitees_for_name)

    url = extract_url(meeting)

    # Filename: "fecha hora - titulo - calendar invitees"
    filename_parts = [
        f"{date_ymd} {time_hm}",
        safe_filename(title),
    ]
    if invitees_for_name:
        filename_parts.append(invitees_for_name)

    stem = " - ".join([p for p in filename_parts if p])
    stem = stem[:MAX_FILENAME_LEN].rstrip()
    out_path = ensure_unique_path(OUTPUT_DIR / f"{stem}.md")

    # Write MD with full meeting metadata + invitees + transcript
    with out_path.open("w", encoding="utf-8") as out:
        out.write(f"# {title}\n\n")

        out.write("## Metadata\n")
        out.write(markdown_kv(meeting, "created_at", created_at_raw))
        out.write(markdown_kv(meeting, "recording_id", meeting.get("recording_id")))
        out.write(markdown_kv(meeting, "meeting_id", meeting.get("id")))
        out.write(markdown_kv(meeting, "url", url))
        out.write(markdown_kv(meeting, "platform", meeting.get("platform")))
        out.write(markdown_kv(meeting, "duration_seconds", meeting.get("duration_seconds")))
        out.write(markdown_kv(meeting, "language", meeting.get("language")))
        out.write(markdown_kv(meeting, "owner", meeting.get("owner")))
        out.write(markdown_kv(meeting, "organizer", meeting.get("organizer")))
        if invitees:
            out.write(markdown_kv(meeting, "calendar_invitees", invitees))

        # Dump the rest of meeting metadata (so you truly have "everything")
        # but avoid duplicating huge transcript-like fields if present.
        out.write("\n### Raw meeting object\n")
        out.write("```json\n")
        out.write(json.dumps(meeting, ensure_ascii=False, indent=2))
        out.write("\n```\n\n")

        out.write("---\n\n")
        out.write("## Transcript\n\n")

        for block in transcript:
            text = (block.get("text") or "").strip()
            if not text:
                continue

            ts = fmt_ts(block.get("start_seconds") or block.get("start_time_seconds") or block.get("start"))
            spk = speaker_name(block)

            if ts:
                out.write(f"- **[{ts}] {spk}:** {text}\n")
            else:
                out.write(f"- **{spk}:** {text}\n")

    print(f"OK -> {out_path.name}")
    converted += 1

print(f"Done. Converted: {converted}. Output: {OUTPUT_DIR.resolve()}")
