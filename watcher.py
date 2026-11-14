"""
Background Folder Watcher for Auto-Uploader (YouTube & Instagram)
All-in-one folder edition: watches the current folder continuously.
"""

import time
import logging
import sys
from uploader import process_queue, load_config, BASE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("auto-watcher")

CHECK_INTERVAL_SECONDS = 30


def main():
    config = load_config()

    active_platforms = []
    if config.get("enable_youtube", True):
        active_platforms.append("YouTube")
    if config.get("enable_instagram", False):
        active_platforms.append("Instagram")

    logger.info("=" * 60)
    logger.info(f"Auto-Uploader Background Watcher Started [{', '.join(active_platforms) or 'None'}]")
    logger.info(f"Root folder: {BASE_DIR}")
    logger.info(f"Watching for videos in: {BASE_DIR / config.get('videos_folder', 'videos')}")
    if config.get("enable_youtube", True):
        logger.info(f"YouTube target release time: {config.get('publish_time', '18:00')}")
    if config.get("enable_instagram", False):
        as_reel = config.get("instagram", {}).get("as_reel", True)
        logger.info(f"Instagram mode: {'Reels' if as_reel else 'Feed Video'}")
    logger.info(f"Checking every {CHECK_INTERVAL_SECONDS} seconds. Press Ctrl+C to stop.")
    logger.info("=" * 60)

    try:
        while True:
            process_queue()
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user.")


if __name__ == "__main__":
    main()
