"""Worker entry point."""

from __future__ import annotations

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
    from pbva_worker.worker_loop import run_worker
    run_worker(settings)


if __name__ == "__main__":
    main()
