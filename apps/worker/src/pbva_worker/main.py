"""Worker entry point."""

from __future__ import annotations

import fcntl
import logging
import sys

from pbva_core.config import Settings


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [worker] %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    lock_path = settings.db_path.parent / "worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.getLogger(__name__).error(
            "Another worker is already running (lock: %s). Exiting.", lock_path
        )
        sys.exit(1)

    from pbva_worker.worker_loop import run_worker
    run_worker(settings)


if __name__ == "__main__":
    main()
