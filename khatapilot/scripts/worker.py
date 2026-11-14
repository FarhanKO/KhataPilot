"""Run KhataPilot's background jobs in their own process, apart from the web server.

    python scripts/worker.py            # due payments, daily checks, alert SMS, backups, every SCHEDULER_INTERVAL_SECONDS
    python scripts/worker.py --once     # one round, then exit (for Task Scheduler or cron)

Set SCHEDULER_IN_WEB=false for the web server when this runs, so a slow request or a restart of the web server
never delays a scheduled payment or an alert. Running both is safe anyway: a lease in the database lets only
one process run the jobs, and each daily alert is marked once.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.bootstrap import build_container  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.infra import logs  # noqa: E402
from app.interface.jobs import hold_lease, run_jobs, scheduler_loop  # noqa: E402

log = logging.getLogger("app.worker")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run one round and exit")
    args = parser.parse_args()
    settings = get_settings()
    logs.configure(log_dir=settings.data_dir / "logs", fmt=settings.log_format, level=settings.log_level,
                   sentry_dsn=settings.sentry_dsn)
    container = build_container(settings)
    interval = max(settings.scheduler_interval_seconds, 5)
    try:
        if args.once:
            if not hold_lease(container, interval):
                log.info("another process is running the jobs; nothing to do")
                return 0
            asyncio.run(run_jobs(container, {}))
            log.info("one round of jobs done")
            return 0
        log.info("worker started: jobs every %ss", interval)
        asyncio.run(scheduler_loop(container, interval))
    except KeyboardInterrupt:
        log.info("worker stopped")
    finally:
        container.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
