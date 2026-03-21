"""DB-backed progress reporter used by the worker to stream events."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from pbva_core.errors import WorkerCancelled


class DbProgressReporter:
    """Writes job_progress events to the DB events table and checks for cancellation."""

    def __init__(self, job_id: str, project_id: str, pass_name: str, session_factory):
        self.job_id = job_id
        self.project_id = project_id
        self.pass_name = pass_name
        self._session_factory = session_factory

    def update(self, fraction: float, stage: str, message: str = "") -> None:
        payload = json.dumps({
            "pass_name": self.pass_name,
            "progress": round(fraction, 3),
            "stage": stage,
            "message": message,
        })
        from pbva_db.models import Event
        with self._session_factory() as session:
            ev = Event(
                project_id=self.project_id,
                job_id=self.job_id,
                event_type="job_progress",
                payload_json=payload,
            )
            session.add(ev)
            session.commit()

    def check_cancelled(self) -> None:
        from pbva_db.models import Job
        with self._session_factory() as session:
            job = session.get(Job, self.job_id)
            if job and job.status == "cancel_requested":
                raise WorkerCancelled(f"Job {self.job_id} was cancelled")
