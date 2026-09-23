#!/usr/bin/env python3
"""
Production YouTube Uploader for Williams Obstetrics (26th Edition).
Uploads videos with resumable chunking, progress reporting, quota limit handling,
and persistent state tracking.
"""

import os
import sys
import json
import time
import argparse
import csv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

REPO_DIR = "/Volumes/Kingston/Gynae"
METADATA_CSV = os.path.join(REPO_DIR, "youtube_metadata.csv")
STATE_FILE = os.path.join(REPO_DIR, "youtube_uploads.json")
DEFAULT_TOKEN = os.path.join(REPO_DIR, "youtube_token.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def load_metadata():
    records = {}
    if not os.path.exists(METADATA_CSV):
        print(f"Error: Metadata CSV not found at {METADATA_CSV}")
        sys.exit(1)
    with open(METADATA_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ch = int(row["chapter"])
            records[ch] = row
    return records

def get_authenticated_service(credentials_path, token_path):
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"Token file invalid ({e}), requesting re-authentication.")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired YouTube OAuth access token...")
            creds.refresh(Request())
        else:
            if not credentials_path or not os.path.exists(credentials_path):
                print(f"Error: Google OAuth credentials file not found: {credentials_path}")
                print("\nPlease download your OAuth Client ID (Desktop App) JSON from Google Cloud Console:")
                print("1. Go to https://console.cloud.google.com/apis/credentials")
                print("2. Create Credentials -> OAuth client ID -> Application type: Desktop app")
                print("3. Download JSON and pass with --credentials /path/to/client_secrets.json\n")
                sys.exit(1)
            print(f"Opening browser for YouTube authorization with: {credentials_path}")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as token:
            token.write(creds.to_json())
        print(f"Saved OAuth credentials to {token_path}")

    return build("youtube", "v3", credentials=creds)

def upload_video(youtube, meta, privacy="public", retries=3):
    ch = int(meta["chapter"])
    video_path = meta["video_path"]
    
    if not os.path.exists(video_path):
        print(f"❌ Error: Video file not found: {video_path}")
        return None

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    tags_list = [t.strip() for t in meta["tags"].split(",") if t.strip()]

    body = {
        "snippet": {
            "title": meta["youtube_title"],
            "description": meta["description"],
            "tags": tags_list[:25],
            "categoryId": str(meta.get("category_id", "27")),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en"
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False
        }
    }

    print(f"\n🚀 [Chapter {ch:02d}/68] Uploading: {meta['youtube_title']}")
    print(f"   File: {video_path} ({size_mb:.1f} MB)")
    print(f"   Privacy: {privacy.upper()}")

    # 5MB chunks for smooth resumable upload
    media = MediaFileUpload(video_path, chunksize=5 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    last_progress = 0
    start_t = time.time()

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                if progress >= last_progress + 10:
                    last_progress = progress
                    elapsed = time.time() - start_t
                    speed_mbps = (size_mb * (progress / 100)) / max(elapsed, 1)
                    print(f"   ⏳ Upload Progress: {progress}% ({speed_mbps:.1f} MB/s)...")
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                print(f"   ⚠️ Server error ({e.resp.status}), retrying in 5 seconds...")
                time.sleep(5)
            elif "uploadLimitExceeded" in str(e) or "quotaExceeded" in str(e):
                print("\n🛑 [YOUTUBE QUOTA EXCEEDED]")
                print("YouTube Data API v3 enforces a daily upload limit (1,600 quota units per video / 10,000 daily free units = ~6 videos/day).")
                print("To upload the remaining chapters today, use Method 1 (Direct drag & drop in studio.youtube.com).")
                print(f"Details: {e}")
                return None
            else:
                raise e

    video_id = response.get("id")
    video_url = f"https://youtu.be/{video_id}"
    print(f"   ✅ Successfully Uploaded!")
    print(f"   🔗 YouTube Link: {video_url}\n")
    return video_id

def main():
    parser = argparse.ArgumentParser(description="Upload Williams Obstetrics read-along videos to YouTube")
    parser.add_argument("--chapter", type=int, help="Single chapter number (1-68)")
    parser.add_argument("--from", dest="from_ch", type=int, help="Start chapter number")
    parser.add_argument("--to", dest="to_ch", type=int, help="End chapter number")
    parser.add_argument("--all", action="store_true", help="Upload all remaining chapters")
    parser.add_argument("--privacy", choices=["public", "unlisted", "private"], default="public", help="Video visibility")
    parser.add_argument("--credentials", default=None, help="Path to Google OAuth client_secrets.json")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Path to cached token.json")
    args = parser.parse_args()

    # Determine credentials path if not provided
    creds_path = args.credentials
    if not creds_path:
        # Search Downloads
        for cand in [
            os.path.join(REPO_DIR, "client_secrets.json"),
            "/Users/rabirajyadav/Downloads/client_secrets.json"
        ]:
            if os.path.exists(cand):
                creds_path = cand
                break

    metadata = load_metadata()
    state = load_state()

    # Determine chapters to process
    if args.chapter:
        target_chapters = [args.chapter]
    elif args.from_ch and args.to_ch:
        target_chapters = list(range(args.from_ch, args.to_ch + 1))
    elif args.all:
        target_chapters = list(range(1, 69))
    else:
        print("Please specify --chapter <N>, --from <X> --to <Y>, or --all")
        print("Example: ./upload_to_youtube.py --chapter 1 --credentials /path/to/client_secrets.json")
        return

    youtube = get_authenticated_service(creds_path, args.token)

    for ch in target_chapters:
        ch_str = str(ch)
        if ch_str in state and "youtube_id" in state[ch_str]:
            prev_id = state[ch_str]["youtube_id"]
            print(f"Chapter {ch:02d} already uploaded: https://youtu.be/{prev_id} (Skipping)")
            continue

        meta = metadata.get(ch)
        if not meta:
            print(f"Skipping unknown chapter {ch}")
            continue

        vid_id = upload_video(youtube, meta, privacy=args.privacy)
        if vid_id:
            state[ch_str] = {
                "youtube_id": vid_id,
                "url": f"https://youtu.be/{vid_id}",
                "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "title": meta["youtube_title"]
            }
            save_state(state)
        else:
            print(f"Upload halted at Chapter {ch:02d}.")
            break

    print("Batch processing complete.")

if __name__ == "__main__":
    main()
