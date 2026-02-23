#!/usr/bin/env python3
"""
Full sync: export new Fathom transcripts and upload them to Google Drive.

  1. Checks the latest transcript date already in the Drive folder
  2. Exports meetings since that date via export_fathom_transcripts_since.py
  3. Uploads the new files to Drive via upload_to_drive.py

Usage:
  python sync.py               # auto-detects date from Drive
  python sync.py --since 2026-01-01  # override date (e.g. first run or backfill)
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]

CONFIG_PATH = Path("config.json")
CREDENTIALS_PATH = Path("credentials.json")
TOKEN_PATH = Path("token.json")

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def load_folder_id():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    folder_id = config.get("google_drive_folder_id", "").strip()
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
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def latest_date_in_drive(service, folder_id) -> str | None:
    names = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(name)",
            pageToken=page_token,
        ).execute()
        names.extend(f["name"] for f in resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    dates = [m.group(1) for name in names if (m := DATE_RE.match(name))]
    return max(dates) if dates else None


def run(cmd):
    print(f"\n> {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None, help="Override start date (YYYY-MM-DD). Required on first run.")
    args = parser.parse_args()

    since = args.since

    if not since:
        print("Connecting to Google Drive to find latest transcript date...")
        folder_id = load_folder_id()
        creds = get_credentials()
        service = build("drive", "v3", credentials=creds)
        since = latest_date_in_drive(service, folder_id)

        if since:
            print(f"Latest date in Drive: {since}")
        else:
            raise SystemExit(
                "No transcripts found in Drive and no --since date provided.\n"
                "Run with: python sync.py --since YYYY-MM-DD"
            )

    run([sys.executable, "export_fathom_transcripts_since.py", "--since", since])
    run([sys.executable, "upload_to_drive.py", "--since", since])

    print("\nSync complete.")


if __name__ == "__main__":
    main()
