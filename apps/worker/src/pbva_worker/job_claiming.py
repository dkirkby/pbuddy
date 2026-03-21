"""Atomic job claiming logic for the worker process."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from pbva_db.models import Job


def claim_next_job(session: Session) -> Job | None:
    """Atomically claim the oldest queued job with no other running job on the same project.

    Returns the claimed Job row (with status='running') or None if nothing is available.
    The caller must commit the session after a successful claim.
    """
    # Find all currently running jobs to exclude their projects.
    running_project_ids = {
        row[0]
        for row in session.execute(
            __import__("sqlalchemy").select(Job.project_id).where(Job.status == "running")
        )
    }

    # Find the oldest queued job not on a blocked project.
    from sqlalchemy import select
    query = (
        select(Job)
        .where(Job.status == "queued")
        .order_by(Job.queued_at.asc())
    )
    for job in session.execute(query).scalars():
        if job.project_id not in running_project_ids:
            job.status = "running"
            job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.claimed_by = str(os.getpid())
            return job

    return None
