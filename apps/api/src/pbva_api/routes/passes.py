"""Pass control endpoints: run, status, corrections, accept."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from pbva_core.enums import ArtifactRole, JobStatus, PassState, ProjectStatus
from pbva_core.types import (
    ArtifactRef,
    JobSummary,
    Pass1CorrectionPayload,
    PassStatusSummary,
)

from pbva_db.models import Artifact, Job, Pass, Project
from pbva_api.dependencies import get_db, get_settings

router = APIRouter(prefix="/api/projects", tags=["passes"])


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.execute(
        select(Project).where(Project.id == project_id).options(selectinload(Project.passes))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_pass_or_404(db: Session, project_id: str, pass_name: str) -> Pass:
    pass_row = db.execute(
        select(Pass)
        .where(Pass.project_id == project_id)
        .where(Pass.pass_name == pass_name)
    ).scalar_one_or_none()
    if pass_row is None:
        raise HTTPException(status_code=404, detail="Pass not found")
    return pass_row


@router.post("/{project_id}/passes/{pass_name}/run", response_model=dict)
def run_pass(
    project_id: str,
    pass_name: str,
    db: Session = Depends(get_db),
):
    project = _get_project_or_404(db, project_id)
    pass_row = _get_pass_or_404(db, project_id, pass_name)

    _VALID_RUN_STATES = {
        PassState.not_started.value,
        PassState.failed.value,
        PassState.waiting_for_user.value,
        PassState.accepted.value,
    }
    if pass_row.state not in _VALID_RUN_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"Pass {pass_name} cannot be run from state '{pass_row.state}'",
        )

    # Create job.
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        project_id=project_id,
        pass_name=pass_name,
        job_type="run_pass",
        status=JobStatus.queued.value,
        queued_at=_utcnow(),
    )
    db.add(job)

    # Reset pass state and clear stale artifact pointers.
    pass_row.state = PassState.queued.value
    pass_row.current_job_id = job_id
    pass_row.latest_raw_artifact_id = None
    pass_row.latest_correction_id = None
    pass_row.latest_accepted_artifact_id = None
    pass_row.updated_at = _utcnow()

    # Reset project status so the UI reflects that the pass is re-running.
    project.status = ProjectStatus.pass1_ready.value
    project.updated_at = _utcnow()

    db.commit()

    return {
        "ok": True,
        "data": JobSummary(
            id=job_id,
            project_id=project_id,
            pass_name=pass_name,
            job_type="run_pass",
            status=JobStatus.queued.value,
            queued_at=job.queued_at,
        ).model_dump(mode="json"),
    }


@router.get("/{project_id}/passes/{pass_name}", response_model=dict)
def get_pass_status(project_id: str, pass_name: str, db: Session = Depends(get_db)):
    pass_row = _get_pass_or_404(db, project_id, pass_name)
    return {
        "ok": True,
        "data": PassStatusSummary(
            pass_name=pass_row.pass_name,
            state=pass_row.state,
            current_job_id=pass_row.current_job_id,
            updated_at=pass_row.updated_at,
        ).model_dump(mode="json"),
    }


@router.get("/{project_id}/passes/{pass_name}/artifacts", response_model=dict)
def list_pass_artifacts(project_id: str, pass_name: str, db: Session = Depends(get_db)):
    artifacts = db.execute(
        select(Artifact)
        .where(Artifact.project_id == project_id)
        .where(Artifact.pass_name == pass_name)
        .order_by(Artifact.created_at)
    ).scalars().all()
    return {
        "ok": True,
        "data": [
            ArtifactRef(
                id=a.id,
                project_id=a.project_id,
                pass_name=a.pass_name,
                artifact_role=a.artifact_role,
                artifact_type=a.artifact_type,
                path=a.path,
                created_at=a.created_at,
            ).model_dump(mode="json")
            for a in artifacts
        ],
    }


@router.get("/{project_id}/passes/pass1/corrections")
def get_pass1_corrections(
    project_id: str,
    db: Session = Depends(get_db),
):
    pass_row = _get_pass_or_404(db, project_id, "pass1")
    if not pass_row.latest_correction_id:
        return {"ok": True, "data": None}
    corr_art = db.get(Artifact, pass_row.latest_correction_id)
    if not corr_art or not Path(corr_art.path).exists():
        return {"ok": True, "data": None}
    import json
    return {"ok": True, "data": json.loads(Path(corr_art.path).read_text())}


@router.put("/{project_id}/passes/pass1/corrections")
def submit_pass1_corrections(
    project_id: str,
    body: dict,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass1")
    if pass_row.state != PassState.waiting_for_user.value:
        raise HTTPException(
            status_code=409,
            detail=f"Pass 1 is in state '{pass_row.state}', not waiting_for_user",
        )

    # Validate correction payload.
    from pbva_pipeline.pass1.run import Pass1
    corrections = Pass1().validate_corrections(body)

    # Persist corrections JSON.
    from pbva_core import paths as p
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass1")
    corrections_dir.mkdir(parents=True, exist_ok=True)
    (corrections_dir / "latest.json").write_text(corrections.model_dump_json(indent=2))

    # Register correction artifact.
    art_id = str(uuid.uuid4())
    art = Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass1",
        artifact_role=ArtifactRole.correction.value,
        artifact_type="json",
        path=str(corrections_dir / "latest.json"),
    )
    db.add(art)
    pass_row.latest_correction_id = art_id
    pass_row.updated_at = _utcnow()
    db.commit()

    return {"ok": True}



@router.post("/{project_id}/passes/pass1/accept")
def accept_pass1(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass1")
    if pass_row.state != PassState.waiting_for_user.value:
        raise HTTPException(
            status_code=409,
            detail=f"Pass 1 is in state '{pass_row.state}', cannot accept",
        )

    # Load raw result.
    from pbva_core import paths as p
    raw_path = p.pass_raw_dir(settings.data_root, project_id, "pass1") / "result.json"
    if not raw_path.exists():
        raise HTTPException(status_code=500, detail="Raw result.json not found")

    from pbva_core.types import Pass1RawResult
    raw_result = Pass1RawResult.model_validate_json(raw_path.read_text())

    # Load corrections if any.
    corrections = None
    if pass_row.latest_correction_id:
        corr_art = db.get(Artifact, pass_row.latest_correction_id)
        if corr_art and Path(corr_art.path).exists():
            from pbva_core.types import Pass1CorrectionPayload
            corrections = Pass1CorrectionPayload.model_validate_json(Path(corr_art.path).read_text())

    # Find median background artifact ID.
    median_bg_artifact_id = ""
    if pass_row.latest_raw_artifact_id:
        median_bg_artifact_id = pass_row.latest_raw_artifact_id

    # Build accepted output.
    from pbva_pipeline.base import NullProgress, PassPaths
    from pbva_pipeline.pass1.run import Pass1

    pass_paths = PassPaths(
        project_root=p.project_root(settings.data_root, project_id),
        uploads_dir=p.uploads_dir(settings.data_root, project_id),
        derived_dir=p.derived_dir(settings.data_root, project_id),
        pass_raw_dir=p.pass_raw_dir(settings.data_root, project_id, "pass1"),
        pass_corrections_dir=p.pass_corrections_dir(settings.data_root, project_id, "pass1"),
        pass_accepted_dir=p.pass_accepted_dir(settings.data_root, project_id, "pass1"),
    )
    project = db.get(Project, project_id)
    from pbva_core.config import Settings as CoreSettings
    from pbva_pipeline.base import PassContext
    ctx = PassContext(
        project_id=project_id,
        project_name=project.name,
        video_path=Path(project.video_path) if project.video_path else pass_paths.original_video,
        video_duration_s=project.video_duration_s or 0.0,
        video_fps=project.video_fps or 30.0,
        video_width=project.video_width or 1920,
        video_height=project.video_height or 1080,
        paths=pass_paths,
        settings=settings,
        job_id=pass_row.current_job_id or "",
        progress=NullProgress(),
    )

    accepted = Pass1().build_accepted_output(ctx, raw_result, corrections, median_bg_artifact_id)

    # Register accepted artifact.
    accepted_path = p.pass_accepted_dir(settings.data_root, project_id, "pass1") / "result.json"
    art_id = str(uuid.uuid4())
    art = Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass1",
        artifact_role=ArtifactRole.accepted.value,
        artifact_type="json",
        path=str(accepted_path),
    )
    db.add(art)

    # Update pass and project state.
    pass_row.state = PassState.accepted.value
    pass_row.latest_accepted_artifact_id = art_id
    pass_row.updated_at = _utcnow()

    project.status = ProjectStatus.pass1_accepted.value
    project.updated_at = _utcnow()

    # Write event.
    from pbva_db.models import Event
    ev = Event(
        project_id=project_id,
        event_type="pass_accepted",
        payload_json=json.dumps({"pass_name": "pass1"}),
    )
    db.add(ev)
    db.commit()

    return {"ok": True, "data": accepted.model_dump(mode="json")}
