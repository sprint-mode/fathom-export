import os
import time
import json
import re
from datetime import datetime
from pathlib import Path
import requests
import random

_config = json.loads(Path("config.json").read_text(encoding="utf-8"))
API_KEY = _config["fathom_api_key"]
BASE = "https://api.fathom.ai/external/v1"
OUT_DIR = os.environ.get("OUT_DIR", "fathom_transcripts")

os.makedirs(OUT_DIR, exist_ok=True)

def safe_filename(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^\w\-\. ]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:180] if len(s) > 180 else s

def fathom_get(path: str, params=None, max_retries=8):
    url = f"{BASE}{path}"
    headers = {"X-Api-Key": API_KEY}

    for attempt in range(max_retries):
        r = requests.get(url, headers=headers, params=params or {}, timeout=60)

        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                wait = float(retry_after)
            else:
                wait = min(60, (2 ** attempt) + random.uniform(0, 1))

            print(f"Rate limited. Waiting {wait:.1f}s...")
            time.sleep(wait)
            continue

        r.raise_for_status()
        return r.json()

    raise Exception("Persistent 429 rate limit")

def list_all_meetings():
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = fathom_get("/meetings", params=params)
        for item in page.get("items", []):
            yield item
        cursor = page.get("next_cursor")
        if not cursor:
            break

def get_transcript(recording_id: int):
    return fathom_get(f"/recordings/{recording_id}/transcript")

def main():
    meetings = list(list_all_meetings())
    print(f"Meetings encontrados: {len(meetings)}")

    for i, m in enumerate(meetings, start=1):
        recording_id = m.get("recording_id")
        if not recording_id:
            continue

        title = m.get("meeting_title") or m.get("title") or f"meeting_{recording_id}"
        created_at = m.get("created_at")  # ISO string
        date_prefix = ""
        if created_at:
            try:
                date_prefix = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except Exception:
                date_prefix = ""

        fname = safe_filename(f"{date_prefix} - {title} - {recording_id}".strip(" -"))
        out_path = os.path.join(OUT_DIR, f"{fname}.json")

        if os.path.exists(out_path):
            print(f"[{i}/{len(meetings)}] SKIP existe: {out_path}")
            continue

        print(f"[{i}/{len(meetings)}] Bajando transcript recording_id={recording_id} ...")
        data = get_transcript(recording_id)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "meeting": m,
                    "transcript": data.get("transcript", data),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        # Respetar rate limit (60/min). Ajustá si también pedís summaries/action items.
        time.sleep(1.1)

    print("OK.")

if __name__ == "__main__":
    main()
