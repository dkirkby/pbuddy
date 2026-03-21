"""Job status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from pbva_core.types import JobSummary
from pbva_db.models import Job
from pbva_api.dependencies import get_db

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=dict)
def get_job(job_id: str, db=Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "ok": True,
        "data": JobSummary(
            id=job.id,
            project_id=job.project_id,
            pass_name=job.pass_name,
            job_type=job.job_type,
            status=job.status,
            queued_at=job.queued_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_message=job.error_message,
        ).model_dump(mode="json"),
    }


@router.get("/projects/{project_id}/jobs", response_model=dict)
def list_project_jobs(project_id: str, db=Depends(get_db)):
    jobs = db.execute(
        select(Job)
        .where(Job.project_id == project_id)
        .order_by(Job.queued_at.desc())
    ).scalars().all()
    return {
        "ok": True,
        "data": [
            JobSummary(
                id=j.id,
                project_id=j.project_id,
                pass_name=j.pass_name,
                job_type=j.job_type,
                status=j.status,
                queued_at=j.queued_at,
                started_at=j.started_at,
                finished_at=j.finished_at,
                error_message=j.error_message,
            ).model_dump(mode="json")
            for j in jobs
        ],
    }
