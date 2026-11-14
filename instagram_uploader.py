"""
Instagram Auto-Uploader Module (Powered by instagrapi)

Handles Instagram authentication, session persistence, caption/hashtag formatting,
and uploading videos as Instagram Reels or standard feed videos.
"""

import os
import sys
import json
import logging
import getpass
from pathlib import Path
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired,
    TwoFactorRequired,
    ChallengeRequired,
    BadPassword,
)

logger = logging.getLogger("ig-uploader")

BASE_DIR = Path(__file__).resolve().parent


def format_instagram_caption(metadata: dict) -> str:
    """
    Combines title, description, and tags into a clean Instagram caption.
    Converts tags to #hashtags if not already prefixed.
    """
    parts = []

    title = (metadata.get("title") or "").strip()
    if title:
        parts.append(title)

    desc = (metadata.get("description") or "").strip()
    # Avoid duplicating title if description is identical or default auto-text
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
    cl.delay_range = [1, 3]

    # 1. Attempt to resume from existing session
    if session_path.exists():
        try:
            logger.info(f"Loading Instagram session from {session_path.name}...")
            cl.load_settings(session_path)
            # Test session validity
            cl.get_timeline_feed()
            user_info = cl.account_info()
            logger.info(f"Instagram session verified! Logged in as: @{user_info.username}")
            return cl
        except LoginRequired:
            logger.warning("Instagram session has expired. Re-authenticating...")
        except Exception as e:
            logger.warning(f"Could not reuse session ({e}). Re-authenticating...")

    # 2. If not interactive and credentials missing, cannot proceed
    if not username and not interactive:
        logger.error("Instagram username not provided in config.json or environment (IG_USERNAME).")
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
        logger.error("Instagram username and password are required for login.")
        return None

    # 3. Perform login
    try:
        logger.info(f"Logging in to Instagram as @{username}...")
        cl.login(username, password)
        cl.dump_settings(session_path)
        logger.info(f"Instagram login successful! Session cached to {session_path.name}")
        return cl

    except TwoFactorRequired:
        logger.info("Two-factor authentication (2FA) is enabled on this account.")
        code = input("Enter Instagram 2FA security code: ").strip()
        try:
            cl.login(username, password, verification_code=code)
            cl.dump_settings(session_path)
            logger.info(f"Instagram 2FA login successful! Session cached to {session_path.name}")
            return cl
        except Exception as e:
            logger.error(f"Instagram 2FA verification failed: {e}")
            return None

    except BadPassword:
        logger.error("Instagram login failed: Incorrect password.")
        return None

    except ChallengeRequired:
        logger.error(
            "Instagram checkpoint/challenge triggered. Please open the Instagram app or "
            "website on this device/network once to confirm your identity, then try again."
        )
        return None

    except Exception as e:
        logger.error(f"Instagram authentication failed: {e}")
        return None


def upload_video_to_instagram(cl: Client, video_path: Path, metadata: dict, as_reel: bool = True) -> str | None:
    """
    Uploads a video to Instagram as a Reel (default) or regular feed video.
    Returns the media URL or None on failure.
    """
    caption = format_instagram_caption(metadata)
    thumb_path = metadata.get("thumbnail_path")

    logger.info(f"Uploading to Instagram ({'Reel' if as_reel else 'Feed Video'}): {video_path.name}")
    if caption:
        first_line = caption.splitlines()[0]
        logger.info(f"  Caption: {first_line[:80]}...")
    if thumb_path and thumb_path.exists():
        logger.info(f"  Custom Cover Thumbnail: {thumb_path.name}")

    try:
        if as_reel:
            media = cl.clip_upload(
                path=video_path,
                caption=caption,
                thumbnail=thumb_path if (thumb_path and thumb_path.exists()) else None,
            )
        else:
            media = cl.video_upload(
                path=video_path,
                caption=caption,
                thumbnail=thumb_path if (thumb_path and thumb_path.exists()) else None,
            )

        code = getattr(media, "code", None)
        url = f"https://www.instagram.com/reel/{code}/" if (as_reel and code) else (f"https://www.instagram.com/p/{code}/" if code else "Uploaded successfully")
        logger.info(f"Successfully uploaded to Instagram! URL: {url}")
        return url

    except Exception as e:
        logger.error(f"Failed to upload {video_path.name} to Instagram: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    from uploader import load_config

    cfg = load_config()
    print("Testing Instagram login & session verification...")
    client = get_instagram_client(cfg, interactive=True)
    if client:
        print("\n[SUCCESS] Instagram account is connected and ready to upload!")
    else:
        print("\n[FAILED] Could not connect to Instagram. Check credentials.")
