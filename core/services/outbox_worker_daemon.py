# -*- coding: utf-8 -*-
"""
Standalone Transactional Outbox Background Worker Daemon.
Can be executed as a dedicated Docker container service or systemd daemon.
Safely processes outbox notifications using PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED.
"""
import time
import signal
import sys
import logging
from core.database import SessionLocal
from core.services.outbox_service import process_outbox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [OUTBOX-DAEMON] %(message)s"
)
logger = logging.getLogger("outbox-daemon")

running = True

def handle_sigterm(signum, frame):
    global running
    logger.info("Received termination signal. Gracefully shutting down outbox daemon...")
    running = False

signal.signal(signal.SIGINT, handle_sigterm)
signal.signal(signal.SIGTERM, handle_sigterm)

def run_daemon(poll_interval: float = 2.0, batch_size: int = 20):
    logger.info(f"Starting standalone Outbox Worker Daemon (interval={poll_interval}s, batch={batch_size})...")
    while running:
        try:
            with SessionLocal() as db:
                result = process_outbox(db, batch_size=batch_size)
                if result.processed_count > 0:
                    logger.info(
                        f"Processed {result.processed_count} notifications: "
                        f"{result.success_count} succeeded, {result.failed_count} failed."
                    )
        except Exception as e:
            logger.error(f"Error in outbox daemon loop: {e}")

        # Sleep in small slices to respond promptly to shutdown signals
        sleep_slices = int(poll_interval / 0.2)
        for _ in range(sleep_slices):
            if not running:
                break
            time.sleep(0.2)

    logger.info("Outbox Worker Daemon stopped cleanly.")

if __name__ == "__main__":
    run_daemon()
