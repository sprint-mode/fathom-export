#!/usr/bin/env python3
"""
Upload Markdown transcripts from ./transcripts/ to a Google Drive folder.
Skips files already present in the Drive folder (matched by filename).

Setup:
  1. Go to https://console.cloud.google.com/ and create a project
  2. Enable the Google Drive API for the project
  3. Go to APIs & Services > Credentials > Create Credentials > OAuth 2.0 Client ID
     - Application type: Desktop app
     - Download the JSON and save it as credentials.json in this folder
  4. Add "google_drive_folder_id" to config.json (the ID at the end of your Drive folder URL)
  5. pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
  6. Run once — a browser window will open to authorize, then token.json is saved for future runs
"""

import argparse
import json
import re
from pathlib import Path

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]

CONFIG_PATH = Path("config.json")
CREDENTIALS_PATH = Path("credentials.json")
TOKEN_PATH = Path("token.json")
TRANSCRIPTS_DIR = Path("transcripts")


def load_config():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    folder_id = config.get("google_drive_folder_id", "").strip()
    if not folder_id:
        raise SystemExit(
            "Missing 'google_drive_folder_id' in config.json.\n"
            "Open the target folder in Google Drive and copy the ID from the URL:\n"
            "  https://drive.google.com/drive/folders/<THIS_PART>\n"
        )
    # Accept full URL or bare ID
    if "folders/" in folder_id:
        folder_id = folder_id.split("folders/")[-1].split("?")[0].rstrip("/")
    return folder_id


def get_credentials():
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise SystemExit(
                    "Missing credentials.json.\n"
                    "Download it from Google Cloud Console:\n"
                    "  APIs & Services > Credentials > OAuth 2.0 Client ID > Download JSON\n"
                    "  Save it as credentials.json in this folder.\n"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def list_existing_files(service, folder_id: str) -> set:
    existing = set()
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(name)",
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            existing.add(f["name"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return existing


def upload_file(service, path: Path, folder_id: str):
    metadata = {"name": path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(path), mimetype="text/markdown", resumable=False)
    service.files().create(body=metadata, media_body=media, fields="id").execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None, help="Only upload files dated on or after YYYY-MM-DD")
    args = parser.parse_args()

    folder_id = load_config()
    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    all_files = sorted(TRANSCRIPTS_DIR.glob("*.md"))
    if args.since:
        files = [f for f in all_files if (m := DATE_RE.match(f.name)) and m.group(1) >= args.since]
        print(f"Filtering to files dated >= {args.since}: {len(files)} of {len(all_files)} local files.")
    else:
        files = all_files

    if not files:
        print(f"No .md files to upload.")
        return

    print(f"Checking Drive folder for existing files...")
    existing = list_existing_files(service, folder_id)
    print(f"{len(existing)} files already in Drive.")

    uploaded = 0
    skipped = 0
    for path in files:
        if path.name in existing:
            print(f"  SKIP  {path.name}")
            skipped += 1
            continue
        print(f"  UP    {path.name}")
        upload_file(service, path, folder_id)
        uploaded += 1

    print(f"\nDone. Uploaded: {uploaded}  Skipped: {skipped}")


if __name__ == "__main__":
    main()
