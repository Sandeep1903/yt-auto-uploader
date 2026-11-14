"""
YouTube Auto-Uploader with Fixed-Time Scheduled Release

One-video-per-day rule: Each run assigns each pending video its own unique
calendar date, starting from today. A local schedule_log.json file tracks
which dates are already booked so re-runs never double-book.

Videos go in the 'videos/' subfolder — keeps scripts and media separate.
"""

import os
import sys
import json
import time
import shutil
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("yt-uploader")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
SCHEDULE_LOG_FILE = BASE_DIR / "schedule_log.json"


def load_config():
    default_config = {
        "publish_time": "18:00",
        "schedule_publish": True,
        "auto_stagger_days": True,
        "videos_folder": "videos",
        "default_tags": ["video"],
        "default_category_id": "22",
        "made_for_kids": False,
        "client_secrets_file": "client_secret.json",
        "token_file": "token.json"
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                default_config.update(user_config)
        except Exception as e:
            logger.warning(f"Could not parse config.json, using defaults: {e}")
    return default_config


def find_client_secrets(config):
    p = BASE_DIR / config.get("client_secrets_file", "client_secret.json")
    if p.exists():
        return p
    for f in BASE_DIR.glob("client_secret*.json"):
        return f
    return None


def get_authenticated_service(config):
    client_secrets_path = find_client_secrets(config)
    token_path = BASE_DIR / config.get("token_file", "token.json")

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            logger.warning(f"Failed to read existing token: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired access token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Refresh failed: {e}. Re-authenticating...")
                creds = None

        if not creds:
            if not client_secrets_path or not client_secrets_path.exists():
                expected = BASE_DIR / "client_secret.json"
                logger.error(
                    f"\n[!] MISSING CREDENTIALS FILE: {expected.name}\n"
                    f"Please place your Google Cloud OAuth client secret JSON file directly in this folder:\n"
                    f"  {expected}\n"
                    f"Refer to README.md for 2-minute instructions.\n"
                )
                return None

            logger.info(f"Opening browser for one-time YouTube authorization using {client_secrets_path.name}...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
        logger.info(f"Saved authentication token to {token_path.name}")

    service = build("youtube", "v3", credentials=creds)
    try:
        channels_response = service.channels().list(mine=True, part="snippet").execute()
        if "items" in channels_response and len(channels_response["items"]) > 0:
            ch = channels_response["items"][0]["snippet"]
            custom_url = ch.get("customUrl", "")
            handle_str = f" ({custom_url})" if custom_url else ""
            logger.info(f"Target YouTube Channel: '{ch['title']}'{handle_str}")
    except Exception as e:
        logger.debug(f"Could not fetch channel details: {e}")

    return service


# ── Schedule log helpers ────────────────────────────────────────────────────

def load_schedule_log() -> set:
    """
    Return a set of already-booked date strings like '2026-11-14'.
    Past dates (before today) are pruned automatically and the file is
    updated on disk so it stays clean over time.
    """
    today = datetime.now().date()
    raw: set = set()

    if SCHEDULE_LOG_FILE.exists():
        try:
            with open(SCHEDULE_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                raw = set(data.get("booked_dates", []))
        except Exception as e:
            logger.warning(f"Could not read schedule_log.json: {e}")

    # Keep only today and future dates
    cleaned = {d for d in raw if datetime.strptime(d, "%Y-%m-%d").date() >= today}

    # If we actually removed something, persist the pruned version right away
    if cleaned != raw:
        removed = sorted(raw - cleaned)
        logger.info(f"Pruned {len(removed)} past date(s) from schedule_log.json: {removed}")
        save_schedule_log(cleaned)

    return cleaned


def save_schedule_log(booked_dates: set):
    """Persist the booked dates back to disk."""
    try:
        sorted_dates = sorted(booked_dates)
        with open(SCHEDULE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump({"booked_dates": sorted_dates}, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save schedule_log.json: {e}")


def ask_custom_date(video_name: str, publish_time_str: str):
    """
    Interactively ask the user whether to set a custom date for this video.
    Returns (rfc3339_utc_str, local_datetime, date_key) if the user picks a date,
    or None if they want automatic scheduling.
    """
    try:
        hour, minute = map(int, publish_time_str.split(":"))
    except Exception:
        hour, minute = 18, 0

    print(f"\n  Video: {video_name}")
    answer = input("  Set a custom date for this video? (y/n): ").strip().lower()
    if answer != "y":
        return None

    while True:
        raw = input("  Enter date (DD/MM/YYYY or YYYY-MM-DD): ").strip()
        dt = None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if dt:
            now_local = datetime.now().astimezone()
            target = now_local.replace(
                year=dt.year, month=dt.month, day=dt.day,
                hour=hour, minute=minute, second=0, microsecond=0
            )
            target_utc = target.astimezone(timezone.utc)
            rfc = target_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            date_key = target.strftime("%Y-%m-%d")
            logger.info(f"  Custom date set: {target.strftime('%Y-%m-%d %I:%M %p %Z')}")
            return rfc, target, date_key
        else:
            print("  Invalid format. Use DD/MM/YYYY (e.g. 15/11/2026) or YYYY-MM-DD.")


def next_free_date(booked_dates: set, publish_time_str: str) -> tuple:
    """
    Find the next calendar day (starting from today) that is NOT already
    in booked_dates, then return (rfc3339_utc_str, local_datetime).

    One-video-per-day rule: each call advances to a unique date and the
    caller must immediately add that date to booked_dates before calling
    again so the next video lands on the following free day.
    """
    now_local = datetime.now().astimezone()
    try:
        hour, minute = map(int, publish_time_str.split(":"))
    except Exception:
        hour, minute = 18, 0

    # Start searching from today
    candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)

    while True:
        date_key = candidate.strftime("%Y-%m-%d")
        if date_key not in booked_dates:
            target_utc = candidate.astimezone(timezone.utc)
            return target_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"), candidate
        candidate += timedelta(days=1)


def parse_metadata(video_path: Path, config: dict):
    base_name = video_path.stem
    title = base_name.replace("_", " ").replace("-", " ").strip()
    description = f"Uploaded automatically via YouTube Auto-Uploader on {datetime.now().strftime('%Y-%m-%d')}."
    tags = list(config.get("default_tags", ["video"]))
    category_id = str(config.get("default_category_id", "22"))
    custom_publish_at = None

    # Check companion .json (e.g. MyVideo.json)
    json_path = video_path.with_suffix(".json")
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                title = meta.get("title", title)
                description = meta.get("description", description)
                tags = meta.get("tags", tags)
                category_id = str(meta.get("category_id", category_id))
                custom_publish_at = meta.get("publish_at", None)
            logger.info(f"Loaded metadata from {json_path.name}")
        except Exception as e:
            logger.warning(f"Error reading {json_path.name}: {e}")

    # Check companion .txt (e.g. MyVideo.txt)
    elif (txt_path := video_path.with_suffix(".txt")).exists():
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                lines = [line.rstrip() for line in f.readlines()]
                if len(lines) >= 1 and lines[0].strip():
                    title = lines[0].strip()
                if len(lines) >= 2 and lines[1].strip():
                    tags = [t.strip() for t in lines[1].split(",") if t.strip()]
                if len(lines) >= 3:
                    description = "\n".join(lines[2:]).strip()
            logger.info(f"Loaded metadata from {txt_path.name}")
        except Exception as e:
            logger.warning(f"Error reading {txt_path.name}: {e}")

    # Check companion thumbnail image (e.g. MyVideo.jpg or MyVideo.png)
    thumbnail_path = None
    for ext in IMAGE_EXTENSIONS:
        thumb_candidate = video_path.with_suffix(ext)
        if thumb_candidate.exists():
            thumbnail_path = thumb_candidate
            break

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": category_id,
        "custom_publish_at": custom_publish_at,
        "thumbnail_path": thumbnail_path
    }


def upload_video_chunked(youtube, video_path: Path, metadata: dict, publish_at_utc: str = None, made_for_kids: bool = False):
    body = {
        "snippet": {
            "title": metadata["title"][:100],
            "description": metadata["description"][:5000],
            "tags": metadata["tags"][:500],
            "categoryId": metadata["category_id"]
        },
        "status": {
            "selfDeclaredMadeForKids": made_for_kids
        }
    }

    if publish_at_utc:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at_utc
    else:
        body["status"]["privacyStatus"] = "private"

    logger.info(f"Starting upload: {video_path.name}")
    logger.info(f"  Title: {body['snippet']['title']}")
    if publish_at_utc:
        logger.info(f"  Scheduled Release: {publish_at_utc} (UTC)")
    else:
        logger.info("  Privacy: private (Unscheduled)")

    media = MediaFileUpload(
        str(video_path),
        chunksize=1024 * 1024 * 10,  # 10MB chunks
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"  Uploading {video_path.name}: {progress}%")
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                logger.warning(f"  Server error {e.resp.status}. Retrying in 5s...")
                time.sleep(5)
            else:
                raise

    video_id = response.get("id")
    logger.info(f"Successfully uploaded! Video ID: {video_id} (https://youtu.be/{video_id})")

    # Upload thumbnail if present
    thumb_path = metadata.get("thumbnail_path")
    if thumb_path and thumb_path.exists():
        try:
            logger.info(f"Uploading thumbnail: {thumb_path.name}...")
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb_path))
            ).execute()
            logger.info("Thumbnail set successfully.")
        except Exception as e:
            logger.warning(f"Thumbnail upload failed (requires verified channel): {e}")

    return video_id


def is_file_ready(file_path: Path, wait_seconds: int = 3) -> bool:
    try:
        size1 = file_path.stat().st_size
        time.sleep(wait_seconds)
        size2 = file_path.stat().st_size
        return size1 == size2 and size1 > 0
    except Exception:
        return False


def mark_files_with_prefix(video_path: Path, prefix: str):
    """
    Renames the video and any companion files (.txt, .json, .jpg, .png)
    with a prefix like '[DONE] ' or '[FAILED] ' so everything stays together.
    """
    base_name = video_path.stem
    parent = video_path.parent

    for item in parent.iterdir():
        if item.is_file() and item.stem == base_name and not item.name.startswith(("[DONE]", "[FAILED]")):
            new_name = f"{prefix} {item.name}"
            new_path = parent / new_name
            try:
                item.rename(new_path)
            except Exception as e:
                logger.warning(f"Could not rename {item.name}: {e}")


def process_queue():
    config = load_config()

    # Resolve the videos folder (default: BASE_DIR/videos)
    videos_folder_name = config.get("videos_folder", "videos")
    video_dir = BASE_DIR / videos_folder_name
    video_dir.mkdir(exist_ok=True)  # auto-create if missing

    # First-time run check: authenticate early if token does not exist yet
    token_path = BASE_DIR / config.get("token_file", "token.json")
    if not token_path.exists():
        logger.info("First-time run detected: Starting one-time YouTube account authorization...")
        youtube = get_authenticated_service(config)
        if not youtube:
            return
        logger.info("[OK] Authorization successful! Your channel is now connected.")
        logger.info(f"Drop videos into the '{videos_folder_name}/' folder to upload them.")

    # Scan the videos subfolder for unprocessed video files
    video_files = [
        f for f in sorted(video_dir.iterdir())
        if f.is_file()
        and f.suffix.lower() in VIDEO_EXTENSIONS
        and not f.name.startswith(("[DONE]", "[FAILED]"))
    ]

    if not video_files:
        logger.info(f"No pending videos found in: {video_dir}")
        return

    logger.info(f"Found {len(video_files)} video(s) ready for upload in '{videos_folder_name}/'")

    if token_path.exists():
        youtube = get_authenticated_service(config)
        if not youtube:
            logger.error("Authentication could not be established. Aborting upload process.")
            return

    # Load the persistent schedule log so we never double-book a date
    booked_dates = load_schedule_log()
    logger.info(f"Already-booked dates: {sorted(booked_dates) or 'none'}")

    for video in video_files:
        logger.info("----------------------------------------")
        logger.info(f"Processing: {video.name}")

        if not is_file_ready(video):
            logger.info(f"File {video.name} is still being written or locked. Skipping for now.")
            continue

        metadata = parse_metadata(video, config)

        publish_at_utc = None
        date_key = None
        if config.get("schedule_publish", True):
            if metadata.get("custom_publish_at"):
                # Honour an explicit publish_at from the companion .json file
                publish_at_utc = metadata["custom_publish_at"]
            else:
                publish_time_str = config.get("publish_time", "18:00")

                # ── Ask user: custom date or auto? ─────────────────────────
                custom = ask_custom_date(video.name, publish_time_str)
                if custom:
                    # User picked a specific date — book it regardless of conflicts
                    publish_at_utc, target_local, date_key = custom
                    booked_dates.add(date_key)
                    save_schedule_log(booked_dates)
                else:
                    # ── One-video-per-day: find next completely free date ──
                    publish_at_utc, target_local = next_free_date(
                        booked_dates,
                        publish_time_str
                    )
                    date_key = target_local.strftime("%Y-%m-%d")
                    booked_dates.add(date_key)
                    save_schedule_log(booked_dates)
                    logger.info(f"Target Scheduled Release: {target_local.strftime('%Y-%m-%d %I:%M %p %Z')}")

        try:
            upload_video_chunked(
                youtube=youtube,
                video_path=video,
                metadata=metadata,
                publish_at_utc=publish_at_utc,
                made_for_kids=config.get("made_for_kids", False)
            )
            mark_files_with_prefix(video, "[DONE]")
            logger.info(f"Upload complete! Marked as '[DONE] {video.name}'")
        except Exception as e:
            logger.error(f"Failed to upload {video.name}: {e}", exc_info=True)
            # Release the reserved date so the slot can be reused next run
            if date_key:
                booked_dates.discard(date_key)
                save_schedule_log(booked_dates)
            mark_files_with_prefix(video, "[FAILED]")


if __name__ == "__main__":
    process_queue()
