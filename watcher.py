"""
Background Folder Watcher for YouTube Auto-Uploader
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
logger = logging.getLogger("yt-watcher")

CHECK_INTERVAL_SECONDS = 30


def main():
    config = load_config()

    logger.info("=" * 60)
    logger.info("YouTube Auto-Uploader Background Watcher Started")
    logger.info(f"Root folder: {BASE_DIR}")
    logger.info(f"Watching for videos in: {BASE_DIR / config.get('videos_folder', 'videos')}")
    logger.info(f"Target release time: {config.get('publish_time', '18:00')}")
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
