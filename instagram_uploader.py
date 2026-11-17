"""
Instagram Auto-Uploader Module (Powered by instagrapi)

Handles Instagram authentication, session persistence, caption/hashtag formatting,
and uploading videos as Instagram Reels or standard feed videos.
"""

import os
import sys
import re
import time
import logging
import getpass
from pathlib import Path
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired,
    TwoFactorRequired,
    ChallengeRequired,
    BadPassword,
    ClientError,
    ClipConfigureError
)

# Silence internal noisy HTTP/API logs from instagrapi and urllib3
logging.getLogger("instagrapi").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger("ig-uploader")

BASE_DIR = Path(__file__).resolve().parent


def format_instagram_caption(metadata: dict) -> str:
    """
    Combines title, description, and tags into a clean Instagram caption.
    Converts tags to #hashtags if not already prefixed.
    Strips out any prefix like [DONE] or [FAILED].
    """
    parts = []

    title = (metadata.get("title") or "").strip()
    title = re.sub(r"^\[(DONE|FAILED)\]\s*", "", title, flags=re.IGNORECASE).strip()
    if title:
        parts.append(title)

    desc = (metadata.get("description") or "").strip()
    # Avoid duplicating title or generic auto-text
    if desc and desc != title and not desc.startswith("Uploaded automatically via"):
        parts.append(desc)

    tags = metadata.get("tags", [])
    if tags:
        hashtags = []
        for tag in tags:
            tag_clean = tag.strip().replace(" ", "")
            if not tag_clean:
                continue
            if not tag_clean.startswith("#"):
                tag_clean = f"#{tag_clean}"
            hashtags.append(tag_clean)
        if hashtags:
            parts.append(" ".join(hashtags))

    return "\n\n".join(parts).strip()


def get_instagram_client(config: dict, interactive: bool = True) -> Client | None:
    """
    Initializes and authenticates the Instagram client.
    Reuses existing session from session_file (default: ig_session.json) if available.
    Prompts for credentials or 2FA only when necessary.
    """
    ig_conf = config.get("instagram", {})
    username = ig_conf.get("username") or os.environ.get("IG_USERNAME", "").strip()
    password = ig_conf.get("password") or os.environ.get("IG_PASSWORD", "").strip()
    session_file_name = ig_conf.get("session_file", "ig_session.json")
    session_path = BASE_DIR / session_file_name

    cl = Client()
    # Keep instagrapi's internal logger quiet
    cl.logger.setLevel(logging.WARNING)
    cl.delay_range = [1, 3]

    # 1. Attempt to resume from existing session
    if session_path.exists():
        try:
            logger.info(f"[Instagram] Loading cached session from {session_path.name}...")
            cl.load_settings(session_path)
            # Verify session validity with a quick check
            cl.get_timeline_feed()
            user_info = cl.account_info()
            logger.info(f"[Instagram] Session valid! Connected as: @{user_info.username}")
            return cl
        except LoginRequired:
            logger.warning("[Instagram] Session expired. Re-authenticating...")
        except Exception as e:
            logger.warning(f"[Instagram] Could not reuse session ({e}). Re-authenticating...")

    # 2. If not interactive and credentials missing, cannot proceed
    if not username and not interactive:
        logger.error("[Instagram] Username not provided in config.json or environment (IG_USERNAME).")
        return None

    # Prompt if credentials are not in config or env
    if not username:
        print("\n" + "=" * 50)
        print(" Instagram Authentication")
        print("=" * 50)
        username = input("Enter your Instagram username: ").strip()

    if not password and interactive:
        password = getpass.getpass("Enter your Instagram password: ")

    if not username or not password:
        logger.error("[Instagram] Username and password are required for login.")
        return None

    # 3. Perform login
    try:
        logger.info(f"[Instagram] Logging in as @{username}...")
        cl.login(username, password)
        cl.dump_settings(session_path)
        logger.info(f"[Instagram] Login successful! Session saved to {session_path.name}")
        return cl

    except TwoFactorRequired:
        logger.info("[Instagram] Two-factor authentication (2FA) is enabled.")
        code = input("Enter Instagram 2FA security code: ").strip()
        try:
            cl.login(username, password, verification_code=code)
            cl.dump_settings(session_path)
            logger.info(f"[Instagram] 2FA login successful! Session saved to {session_path.name}")
            return cl
        except Exception as e:
            logger.error(f"[Instagram] 2FA verification failed: {e}")
            return None

    except BadPassword:
        logger.error("[Instagram] Login failed: Incorrect password.")
        return None

    except ChallengeRequired:
        logger.error(
            "[Instagram] Checkpoint/challenge triggered. Please open the Instagram app or "
            "website on this device/network once to confirm your identity, then try again."
        )
        return None

    except Exception as e:
        logger.error(f"[Instagram] Authentication failed: {e}")
        return None


def upload_video_to_instagram(cl: Client, video_path: Path, metadata: dict, as_reel: bool = True) -> str | None:
    """
    Uploads a video to Instagram as a Reel (default) or regular feed video.
    Returns the media URL or None on failure.
    """
    caption = format_instagram_caption(metadata)
    thumb_path = metadata.get("thumbnail_path")

    mode_label = "Reel" if as_reel else "Feed Video"
    logger.info(f"[Instagram] Uploading {video_path.name} as {mode_label}...")
    if caption:
        first_line = caption.splitlines()[0]
        logger.info(f"  Caption: {first_line[:80]}...")
    if thumb_path and thumb_path.exists():
        logger.info(f"  Custom Cover Thumbnail: {thumb_path.name}")

    try:
        logger.info("  Sending video data to Instagram servers...")
        if as_reel:
            logger.info("  Processing & transcoding Reel (takes ~30-60s on Instagram)...")
            media = cl.clip_upload(
                path=video_path,
                caption=caption,
                thumbnail=thumb_path if (thumb_path and thumb_path.exists()) else None,
                configure_timeout=15
            )
        else:
            logger.info("  Processing video on Instagram...")
            media = cl.video_upload(
                path=video_path,
                caption=caption,
                thumbnail=thumb_path if (thumb_path and thumb_path.exists()) else None,
            )

        code = getattr(media, "code", None)
        url = f"https://www.instagram.com/reel/{code}/" if (as_reel and code) else (f"https://www.instagram.com/p/{code}/" if code else "Uploaded successfully")
        logger.info(f"[Instagram] Successfully uploaded! URL: {url}")

        # Clean up any temporary auto-generated thumbnail instagrapi created
        auto_thumb = video_path.parent / f"{video_path.name}.jpg"
        if auto_thumb.exists() and (not thumb_path or auto_thumb != thumb_path):
            try:
                auto_thumb.unlink()
            except Exception:
                pass

        return url

    except Exception as e:
        logger.error(f"[Instagram] Upload failed for {video_path.name}: {e}")
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    from uploader import load_config

    cfg = load_config()
    print("Testing Instagram connection...")
    client = get_instagram_client(cfg, interactive=True)
    if client:
        print("\n[SUCCESS] Instagram account connected and verified!")
    else:
        print("\n[FAILED] Could not connect to Instagram. Check credentials in config.json.")
