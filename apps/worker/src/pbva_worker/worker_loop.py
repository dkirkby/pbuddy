"""Main worker loop: poll for jobs, claim, execute, update state."""

from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pbva_core.config import Settings
from pbva_core.enums import PassState, ProjectStatus
from pbva_core.errors import WorkerCancelled
from pbva_db.engine import get_engine, get_session_factory, init_db
from pbva_db.models import Artifact, Event, Job, Pass, Project

from .execution_context import build_pass_context
from .job_claiming import claim_next_job
from .progress import DbProgressReporter

logger = logging.getLogger(__name__)

# Registry of pass implementations keyed by pass_name.
_PASS_REGISTRY: dict = {}


def _get_pass(pass_name: str):
    if pass_name not in _PASS_REGISTRY:
        if pass_name == "pass1":
            from pbva_pipeline.pass1.run import Pass1
            _PASS_REGISTRY["pass1"] = Pass1()
        elif pass_name == "pass2":
            from pbva_pipeline.pass2.run import Pass2
            _PASS_REGISTRY["pass2"] = Pass2()
        elif pass_name == "pass3":
            from pbva_pipeline.pass3.run import Pass3
            _PASS_REGISTRY["pass3"] = Pass3()
        elif pass_name == "pass4":
            from pbva_pipeline.pass4.run import Pass4
            _PASS_REGISTRY["pass4"] = Pass4()
        else:
            raise ValueError(f"Unknown pass: {pass_name}")
    return _PASS_REGISTRY[pass_name]


_PASS_WAITING_STATUS = {
    "pass1": ProjectStatus.pass1_waiting_for_review,
    "pass2": ProjectStatus.pass2_waiting_for_review,
    "pass3": ProjectStatus.pass3_waiting_for_review,
    "pass4": ProjectStatus.pass4_waiting_for_review,
}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _write_event(session, project_id: str, job_id: str | None, event_type: str, payload: dict):
    ev = Event(
        project_id=project_id,
        job_id=job_id,
        event_type=event_type,
        payload_json=json.dumps(payload),
    )
    session.add(ev)


def _register_artifacts(session, project_id: str, pass_name: str, job_id: str, artifacts: list[dict]) -> list[str]:
    """Write artifact rows to DB and return the list of artifact IDs."""
    ids = []
    for art in artifacts:
        art_id = str(uuid.uuid4())
        row = Artifact(
            id=art_id,
            project_id=project_id,
            pass_name=pass_name,
            artifact_role=art["role"],
            artifact_type=art["type"],
            path=art["path"],
            job_id=job_id,
        )
        session.add(row)
        ids.append(art_id)
    return ids


def execute_job(job: Job, settings: Settings, session_factory) -> None:
    """Run one job to completion, updating DB state throughout."""
    pass_impl = _get_pass(job.pass_name)

    with session_factory() as session:
        project = session.get(Project, job.project_id)
        if project is None:
            raise RuntimeError(f"Project {job.project_id} not found")

        progress = DbProgressReporter(
            job_id=job.id,
            project_id=job.project_id,
            pass_name=job.pass_name,
            session_factory=session_factory,
        )

        ctx = build_pass_context(job, project, settings, session_factory, progress)

    # --- Mark pass as running ---
    with session_factory() as session:
        pass_row = session.execute(
            __import__("sqlalchemy").select(Pass)
            .where(Pass.project_id == job.project_id)
            .where(Pass.pass_name == job.pass_name)
        ).scalar_one_or_none()
        if pass_row:
            pass_row.state = PassState.running.value
            pass_row.updated_at = _utcnow()
            session.commit()

    # --- Validate inputs ---
    pass_impl.validate_inputs(ctx)

    # --- Run the pass ---
    logger.info("Starting %s job=%s project=%s", job.pass_name, job.id, job.project_id)
    raw_result = pass_impl.run(ctx, progress=ctx.progress)

    # --- Write artifacts to DB ---
    artifact_dicts = pass_impl.write_raw_outputs(ctx, raw_result)
    with session_factory() as session:
        art_ids = _register_artifacts(session, job.project_id, job.pass_name, job.id, artifact_dicts)

        # Find the median background artifact ID (Pass 1 only).
        median_bg_artifact_id = ""
        if job.pass_name == "pass1":
            for i, art in enumerate(artifact_dicts):
                if art["type"] == "png" and "median_background" in art["path"]:
                    median_bg_artifact_id = art_ids[i]
                    break

        # Update pass state.
        pass_row = session.execute(
            __import__("sqlalchemy").select(Pass)
            .where(Pass.project_id == job.project_id)
            .where(Pass.pass_name == job.pass_name)
        ).scalar_one()
        pass_row.state = PassState.waiting_for_user.value
        if art_ids:
            pass_row.latest_raw_artifact_id = art_ids[0]
        pass_row.updated_at = _utcnow()

        # Update project status.
        project = session.get(Project, job.project_id)
        waiting_status = _PASS_WAITING_STATUS.get(job.pass_name)
        if waiting_status is not None:
            project.status = waiting_status.value
            project.updated_at = _utcnow()

        # Mark job succeeded.
        job_row = session.get(Job, job.id)
        job_row.status = "succeeded"
        job_row.finished_at = _utcnow()

        _write_event(session, job.project_id, job.id, "pass_waiting_for_user", {
            "pass_name": job.pass_name,
            "median_bg_artifact_id": median_bg_artifact_id,
        })
        session.commit()

    logger.info("Completed %s job=%s", job.pass_name, job.id)


def _recover_stale_jobs(session_factory) -> None:
    """Reset any jobs left in 'running' state from a previous worker process.

    This handles the case where the worker was killed mid-job.  The job is reset
    to 'queued' so it will be picked up and re-executed on the next poll cycle.
    """
    from sqlalchemy import select, update
    with session_factory() as session:
        stale = session.execute(
            select(Job).where(Job.status == "running")
        ).scalars().all()
        if not stale:
            return
        for job in stale:
            logger.warning(
                "Recovering stale job=%s pass=%s project=%s (was running, resetting to queued)",
                job.id, job.pass_name, job.project_id,
            )
            job.status = "queued"
            job.started_at = None
            job.claimed_by = None
        session.commit()


def run_worker(settings: Settings) -> None:
    """Blocking worker loop; polls for jobs and executes them."""
    engine = get_engine(settings.db_path)
    init_db(engine)
    session_factory = get_session_factory(engine)

    _recover_stale_jobs(session_factory)
    logger.info("Worker started, polling every %.1fs", settings.worker_poll_interval_s)

    while True:
        try:
            with session_factory() as session:
                job = claim_next_job(session)
                if job is None:
                    session.commit()
                    time.sleep(settings.worker_poll_interval_s)
                    continue
                session.commit()
                # Detach the job object from the session so we can use it outside.
                job_id = job.id
                project_id = job.project_id
                pass_name = job.pass_name

            # Reload outside the claim session.
            with session_factory() as session:
                job = session.get(Job, job_id)

            logger.info("Claimed job=%s pass=%s project=%s", job_id, pass_name, project_id)
            execute_job(job, settings, session_factory)

        except WorkerCancelled as e:
            logger.warning("Job cancelled: %s", e)
            with session_factory() as session:
                job_row = session.get(Job, job_id)
                if job_row:
                    job_row.status = "cancelled"
                    job_row.finished_at = _utcnow()
                # Pass state was already reset to not_started by the upstream
                # run_pass endpoint, so no change needed here.
                session.commit()

        except Exception:
            tb = traceback.format_exc()
            logger.error("Job failed:\n%s", tb)
            try:
                with session_factory() as session:
                    job_row = session.get(Job, job_id)
                    if job_row:
                        job_row.status = "failed"
                        job_row.finished_at = _utcnow()
                        job_row.error_message = tb[-2000:]  # truncate

                        pass_row = session.execute(
                            __import__("sqlalchemy").select(Pass)
                            .where(Pass.project_id == project_id)
                            .where(Pass.pass_name == pass_name)
                        ).scalar_one_or_none()
                        if pass_row:
                            pass_row.state = PassState.failed.value
                            pass_row.updated_at = _utcnow()

                        _write_event(session, project_id, job_id, "job_failed", {"error": tb[-500:]})
                        session.commit()
            except Exception:
                logger.exception("Failed to record job failure")
