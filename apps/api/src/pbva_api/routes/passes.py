"""Pass control endpoints: run, status, corrections, accept."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from pbva_core.dependencies import RUN_DEPENDENTS, SETTINGS_DEPENDENTS
from pbva_core.enums import ArtifactRole, JobStatus, PassState, ProjectStatus
from pbva_core.types import (
    ArtifactRef,
    JobSummary,
    Pass1CorrectionPayload,
    PassStatusSummary,
)

from pbva_db.models import Artifact, Event, Job, Pass, Project
from pbva_api.dependencies import get_db, get_settings


router = APIRouter(prefix="/api/projects", tags=["passes"])

# States in which a pass is actively working — dirty marking is deferred until done.
_IN_FLIGHT = {PassState.queued.value, PassState.running.value}


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rebuild_pass1_accepted(db: Session, project_id: str, settings) -> None:
    """Regenerate pass1 accepted artifacts (tent_mask.png + result.json) in-place.

    Called both from accept_pass1 and from submit_pass1_corrections when pass1 is
    already accepted, so that court-corner changes take effect immediately without
    requiring an explicit re-accept.
    """
    from pbva_core import paths as p
    from pbva_core.types import Pass1RawResult, Pass1CorrectionPayload
    from pbva_pipeline.base import NullProgress, PassPaths, PassContext
    from pbva_pipeline.pass1.run import Pass1

    pass_row = db.execute(
        select(Pass)
        .where(Pass.project_id == project_id)
        .where(Pass.pass_name == "pass1")
    ).scalar_one_or_none()
    if pass_row is None:
        return

    raw_path = p.pass_raw_dir(settings.data_root, project_id, "pass1") / "result.json"
    if not raw_path.exists():
        return  # raw output not yet produced; nothing to regenerate

    raw_result = Pass1RawResult.model_validate_json(raw_path.read_text())

    corrections = None
    if pass_row.latest_correction_id:
        corr_art = db.get(Artifact, pass_row.latest_correction_id)
        if corr_art and Path(corr_art.path).exists():
            corrections = Pass1CorrectionPayload.model_validate_json(Path(corr_art.path).read_text())

    median_bg_artifact_id = pass_row.latest_raw_artifact_id or ""

    project = db.get(Project, project_id)
    pass_paths = PassPaths(
        project_root=p.project_root(settings.data_root, project_id),
        uploads_dir=p.uploads_dir(settings.data_root, project_id),
        derived_dir=p.derived_dir(settings.data_root, project_id),
        pass_raw_dir=p.pass_raw_dir(settings.data_root, project_id, "pass1"),
        pass_corrections_dir=p.pass_corrections_dir(settings.data_root, project_id, "pass1"),
        pass_accepted_dir=p.pass_accepted_dir(settings.data_root, project_id, "pass1"),
    )
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
    Pass1().build_accepted_output(ctx, raw_result, corrections, median_bg_artifact_id)


def _mark_settings_dirty(db: Session, project_id: str, pass_name: str) -> None:
    """Mark downstream passes dirty after pass_name's settings are saved."""
    for downstream in SETTINGS_DEPENDENTS.get(pass_name, []):
        ds_row = db.execute(
            select(Pass)
            .where(Pass.project_id == project_id)
            .where(Pass.pass_name == downstream)
        ).scalar_one_or_none()
        if ds_row is None or ds_row.state in _IN_FLIGHT:
            continue
        if ds_row.state != PassState.not_started.value:
            ds_row.is_dirty = True
            ds_row.updated_at = _utcnow()


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
    settings=Depends(get_settings),
):
    project = _get_project_or_404(db, project_id)
    pass_row = db.execute(
        select(Pass)
        .where(Pass.project_id == project_id)
        .where(Pass.pass_name == pass_name)
    ).scalar_one_or_none()
    if pass_row is None:
        # Project pre-dates this pass being added — create the row on demand.
        pass_row = Pass(
            id=str(uuid.uuid4()),
            project_id=project_id,
            pass_name=pass_name,
            state=PassState.not_started.value,
            updated_at=_utcnow(),
        )
        db.add(pass_row)
        db.flush()

    # Any pass state is valid to re-run; downstream cascade handles consistency.
    # (produced_raw_output is unused but included defensively.)

    # If pass4 is paused, remove the sentinel so the stalled job can finish
    # and the worker can then pick up the new job.
    if pass_name == "pass4":
        _pass4_pause_file(settings.data_root, project_id).unlink(missing_ok=True)

    # Mark downstream passes dirty so the UI shows Re-Run buttons.
    # If pass4 is actively running, cancel it first (can't mix mid-run results
    # with new upstream inputs).
    for downstream in RUN_DEPENDENTS.get(pass_name, []):
        ds_row = db.execute(
            select(Pass)
            .where(Pass.project_id == project_id)
            .where(Pass.pass_name == downstream)
        ).scalar_one_or_none()
        if ds_row is None or ds_row.state == PassState.not_started.value:
            continue
        # Cancel a running pass4 job so the worker can pick up the new upstream job.
        if ds_row.state in _IN_FLIGHT:
            if downstream == "pass4" and ds_row.current_job_id:
                ds_job = db.get(Job, ds_row.current_job_id)
                if ds_job and ds_job.status in ("running", "queued"):
                    ds_job.status = "cancel_requested"
                    _pass4_pause_file(settings.data_root, project_id).unlink(missing_ok=True)
        # Only mark dirty if not already in flight (in-flight pass will be marked
        # dirty when it completes, or user can re-run again after it finishes).
        if ds_row.state not in _IN_FLIGHT:
            ds_row.is_dirty = True
            ds_row.updated_at = _utcnow()

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

    # Reset pass state and clear stale artifact pointers for this pass.
    # Downstream passes keep their state and artifacts (they're just marked dirty).
    pass_row.state = PassState.queued.value
    pass_row.is_dirty = False
    pass_row.current_job_id = job_id
    pass_row.latest_raw_artifact_id = None
    pass_row.latest_correction_id = None
    pass_row.latest_accepted_artifact_id = None
    pass_row.updated_at = _utcnow()

    # Update project status to in_progress to reflect active work.
    project.status = ProjectStatus.in_progress.value
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
    if pass_row.state in _IN_FLIGHT:
        raise HTTPException(
            status_code=409,
            detail=f"Pass 1 is currently {pass_row.state}; wait for it to finish before saving corrections",
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

    # If pass1 is already accepted, regenerate tent_mask.png immediately so that
    # downstream passes receive up-to-date accepted artifacts without a manual re-accept.
    if pass_row.state == PassState.accepted.value:
        _rebuild_pass1_accepted(db, project_id, settings)

    _mark_settings_dirty(db, project_id, "pass1")
    db.commit()

    return {"ok": True}


@router.post("/{project_id}/passes/pass1/accept")
def accept_pass1(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass1")
    if pass_row.state not in (PassState.waiting_for_user.value, PassState.accepted.value):
        raise HTTPException(
            status_code=409,
            detail=f"Pass 1 is in state '{pass_row.state}', cannot accept",
        )

    from pbva_core import paths as p
    raw_path = p.pass_raw_dir(settings.data_root, project_id, "pass1") / "result.json"
    if not raw_path.exists():
        raise HTTPException(status_code=500, detail="Raw result.json not found")

    # Regenerate accepted artifacts (tent_mask.png + result.json).
    _rebuild_pass1_accepted(db, project_id, settings)

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
    pass_row.is_dirty = False
    pass_row.latest_accepted_artifact_id = art_id
    pass_row.updated_at = _utcnow()

    project = db.get(Project, project_id)
    project.status = ProjectStatus.in_progress.value
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

    return {"ok": True}


@router.get("/{project_id}/passes/pass2/corrections")
def get_pass2_corrections(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    import base64
    # Return empty if corrections were cleared by a re-run.
    pass_row = _get_pass_or_404(db, project_id, "pass2")
    if not pass_row.latest_correction_id:
        return {"ok": True, "data": {"annotations": {}, "patches": {}}}

    from pbva_core import paths as p
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass2")
    ann_path = corrections_dir / "annotations.json"
    data = json.loads(ann_path.read_text()) if ann_path.exists() else {"annotations": {}}

    patches: dict[str, str] = {}
    patches_dir = corrections_dir / "patches" / "raw"
    if patches_dir.exists():
        for png_path in sorted(patches_dir.glob("*.png")):
            frame_str = str(int(png_path.stem))
            b64 = base64.b64encode(png_path.read_bytes()).decode()
            patches[frame_str] = f"data:image/png;base64,{b64}"

    rally_path = corrections_dir / "rally.json"
    rally_json = json.loads(rally_path.read_text()) if rally_path.exists() else {}

    result: dict = {**data, "patches": patches}
    if "player_names" in rally_json:
        result["player_names"] = rally_json["player_names"]
    if "far_team_serves_first" in rally_json:
        result["far_team_serves_first"] = rally_json["far_team_serves_first"]
    if "rally" in rally_json:
        result["rally"] = rally_json["rally"]
    return {"ok": True, "data": result}


@router.put("/{project_id}/passes/pass2/corrections")
def save_pass2_corrections(
    project_id: str,
    body: dict,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    import base64
    pass_row = _get_pass_or_404(db, project_id, "pass2")
    if pass_row.state in _IN_FLIGHT:
        raise HTTPException(
            status_code=409,
            detail=f"Pass 2 is currently {pass_row.state}; wait for it to finish before saving corrections",
        )

    from pbva_pipeline.pass2.run import Pass2
    corrections = Pass2().validate_corrections({
        "annotations": body.get("annotations", {}),
    })

    from pbva_core import paths as p
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass2")
    corrections_dir.mkdir(parents=True, exist_ok=True)

    ann_data = {k: {"x": v.x, "y": v.y, "radius": v.radius} for k, v in corrections.annotations.items()}
    ann_path = corrections_dir / "annotations.json"
    ann_path.write_text(json.dumps({"annotations": ann_data}, indent=2))

    # Write patches: clear old files, write the complete current set.
    patches_dir = corrections_dir / "patches" / "raw"
    if patches_dir.exists():
        for old_png in patches_dir.glob("*.png"):
            old_png.unlink()
    patches_dir.mkdir(parents=True, exist_ok=True)
    for frame_str, data_url in body.get("patches", {}).items():
        _header, b64_data = data_url.split(",", 1)
        (patches_dir / f"{int(frame_str):06d}.png").write_bytes(base64.b64decode(b64_data))

    # Write rally.json with player names, serve-order flag, and rally records if any is provided.
    if "player_names" in body or "far_team_serves_first" in body or "rally" in body:
        rally_path = corrections_dir / "rally.json"
        rally_data: dict = {}
        if "player_names" in body:
            rally_data["player_names"] = body["player_names"]
        if "far_team_serves_first" in body:
            rally_data["far_team_serves_first"] = body["far_team_serves_first"]
        if "rally" in body:
            rally_data["rally"] = body["rally"]
        rally_path.write_text(json.dumps(rally_data, indent=2))

    # Register correction artifact.
    art_id = str(uuid.uuid4())
    db.add(Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass2",
        artifact_role=ArtifactRole.correction.value,
        artifact_type="json",
        path=str(ann_path),
    ))
    pass_row.latest_correction_id = art_id
    pass_row.updated_at = _utcnow()

    _mark_settings_dirty(db, project_id, "pass2")
    db.commit()

    return {"ok": True}


@router.post("/{project_id}/passes/pass2/reset")
def reset_pass2(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    """Clear all Pass 2 annotations and reset to waiting_for_user so the user can re-annotate."""
    pass_row = _get_pass_or_404(db, project_id, "pass2")
    if pass_row.state not in (PassState.waiting_for_user.value, PassState.accepted.value):
        raise HTTPException(
            status_code=409,
            detail=f"Pass 2 is in state '{pass_row.state}', cannot reset",
        )

    from pbva_core import paths as p
    import shutil

    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass2")
    if corrections_dir.exists():
        shutil.rmtree(corrections_dir)

    accepted_dir = p.pass_accepted_dir(settings.data_root, project_id, "pass2")
    if accepted_dir.exists():
        shutil.rmtree(accepted_dir)

    pass_row.latest_correction_id = None
    pass_row.latest_accepted_artifact_id = None
    pass_row.state = PassState.waiting_for_user.value
    pass_row.is_dirty = False
    pass_row.updated_at = _utcnow()

    for downstream in RUN_DEPENDENTS.get("pass2", []):
        ds_row = db.execute(
            select(Pass)
            .where(Pass.project_id == project_id)
            .where(Pass.pass_name == downstream)
        ).scalar_one_or_none()
        if ds_row is None or ds_row.state == PassState.not_started.value:
            continue
        if ds_row.state not in _IN_FLIGHT:
            ds_row.is_dirty = True
            ds_row.updated_at = _utcnow()

    db.commit()
    return {"ok": True}


@router.post("/{project_id}/passes/pass2/accept")
def accept_pass2(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass2")
    if pass_row.state not in (PassState.waiting_for_user.value, PassState.accepted.value):
        raise HTTPException(
            status_code=409,
            detail=f"Pass 2 is in state '{pass_row.state}', cannot accept",
        )

    from pbva_core import paths as p

    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass2")
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass2")
    accepted_dir = p.pass_accepted_dir(settings.data_root, project_id, "pass2")
    accepted_dir.mkdir(parents=True, exist_ok=True)

    # Load raw result for metadata.
    raw_result_path = raw_dir / "result.json"
    if not raw_result_path.exists():
        raise HTTPException(status_code=500, detail="Raw result.json not found")
    from pbva_core.types import Pass2RawResult, Pass2CorrectionPayload
    raw_result = Pass2RawResult.model_validate_json(raw_result_path.read_text())

    # Load corrections (annotations).
    corrections = None
    ann_path = corrections_dir / "annotations.json"
    if ann_path.exists():
        corrections = Pass2CorrectionPayload.model_validate_json(ann_path.read_text())

    # Build accepted output via the pass implementation.
    from pbva_pipeline.pass2.run import Pass2
    from pbva_pipeline.base import NullProgress, PassPaths, PassContext
    from pbva_core.config import Settings as CoreSettings

    project = db.get(Project, project_id)
    pass_paths = PassPaths(
        project_root=p.project_root(settings.data_root, project_id),
        uploads_dir=p.uploads_dir(settings.data_root, project_id),
        derived_dir=p.derived_dir(settings.data_root, project_id),
        pass_raw_dir=raw_dir,
        pass_corrections_dir=corrections_dir,
        pass_accepted_dir=accepted_dir,
    )
    ctx = PassContext(
        project_id=project_id,
        project_name=project.name,
        video_path=p.project_root(settings.data_root, project_id) / "uploads" / "original.mp4",
        video_duration_s=project.video_duration_s or 0.0,
        video_fps=project.video_fps or 30.0,
        video_width=project.video_width or 1920,
        video_height=project.video_height or 1080,
        paths=pass_paths,
        settings=settings,
        job_id=pass_row.current_job_id or "",
        progress=NullProgress(),
    )
    accepted = Pass2().build_accepted_output(ctx, raw_result, corrections)

    # Copy rally.json to accepted dir if present.
    rally_src = corrections_dir / "rally.json"
    if rally_src.exists():
        import shutil
        shutil.copy2(rally_src, accepted_dir / "rally.json")

    # Register accepted artifacts.
    art_id = str(uuid.uuid4())
    db.add(Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass2",
        artifact_role=ArtifactRole.accepted.value,
        artifact_type="json",
        path=str(accepted_dir / "result.json"),
    ))
    db.add(Artifact(
        id=str(uuid.uuid4()),
        project_id=project_id,
        pass_name="pass2",
        artifact_role=ArtifactRole.accepted.value,
        artifact_type="json",
        path=str(accepted_dir / "annotations.json"),
    ))

    pass_row.state = PassState.accepted.value
    pass_row.is_dirty = False
    pass_row.latest_accepted_artifact_id = art_id
    pass_row.updated_at = _utcnow()

    project.status = ProjectStatus.in_progress.value
    project.updated_at = _utcnow()

    db.add(Event(
        project_id=project_id,
        event_type="pass_accepted",
        payload_json=json.dumps({"pass_name": "pass2"}),
    ))
    db.commit()

    return {"ok": True, "data": accepted.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Pass 3 — Ball Color Tagging
# ---------------------------------------------------------------------------

_PASS3_MIME = {"png": "image/png", "json": "application/json", "csv": "text/csv"}


@router.get("/{project_id}/passes/pass3/raw/{filename}")
def get_pass3_raw_file(
    project_id: str,
    filename: str,
    settings=Depends(get_settings),
):
    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass3")
    path = (raw_dir / filename).resolve()
    try:
        path.relative_to(raw_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    suffix = path.suffix.lstrip(".")
    if suffix == "json":
        return JSONResponse(content=json.loads(path.read_text()))
    return FileResponse(str(path), media_type=_PASS3_MIME.get(suffix, "application/octet-stream"))


@router.get("/{project_id}/passes/pass3/corrections")
def get_pass3_corrections(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    _get_pass_or_404(db, project_id, "pass3")
    from pbva_core import paths as p
    poly_path = p.pass_corrections_dir(settings.data_root, project_id, "pass3") / "ball_color_polygons.json"
    if not poly_path.exists():
        return {"ok": True, "data": None}
    return {"ok": True, "data": json.loads(poly_path.read_text())}


@router.put("/{project_id}/passes/pass3/corrections")
def save_pass3_corrections(
    project_id: str,
    body: dict,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass3")
    if pass_row.state not in (PassState.waiting_for_user.value, PassState.accepted.value):
        raise HTTPException(status_code=409, detail=f"Pass 3 is in state '{pass_row.state}'")

    from pbva_pipeline.pass3.run import Pass3
    corrections = Pass3().validate_corrections(body)

    from pbva_core import paths as p
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass3")
    corrections_dir.mkdir(parents=True, exist_ok=True)
    poly_path = corrections_dir / "ball_color_polygons.json"
    poly_path.write_text(json.dumps(corrections, indent=2))

    art_id = str(uuid.uuid4())
    db.add(Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass3",
        artifact_role=ArtifactRole.correction.value,
        artifact_type="json",
        path=str(poly_path),
    ))
    pass_row.latest_correction_id = art_id
    pass_row.updated_at = _utcnow()

    _mark_settings_dirty(db, project_id, "pass3")
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/passes/pass3/accept")
def accept_pass3(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass3")
    if pass_row.state not in (PassState.waiting_for_user.value, PassState.accepted.value):
        raise HTTPException(status_code=409, detail=f"Pass 3 is in state '{pass_row.state}', cannot accept")

    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass3")
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass3")
    accepted_dir = p.pass_accepted_dir(settings.data_root, project_id, "pass3")
    accepted_dir.mkdir(parents=True, exist_ok=True)

    poly_path = corrections_dir / "ball_color_polygons.json"
    corrections = json.loads(poly_path.read_text()) if poly_path.exists() else None

    from pbva_pipeline.pass3.run import Pass3
    from pbva_pipeline.base import NullProgress, PassPaths, PassContext

    project = db.get(Project, project_id)
    ctx = PassContext(
        project_id=project_id,
        project_name=project.name,
        video_path=p.project_root(settings.data_root, project_id) / "uploads" / "original.mp4",
        video_duration_s=project.video_duration_s or 0.0,
        video_fps=project.video_fps or 30.0,
        video_width=project.video_width or 1920,
        video_height=project.video_height or 1080,
        paths=PassPaths(
            project_root=p.project_root(settings.data_root, project_id),
            uploads_dir=p.uploads_dir(settings.data_root, project_id),
            derived_dir=p.derived_dir(settings.data_root, project_id),
            pass_raw_dir=raw_dir,
            pass_corrections_dir=corrections_dir,
            pass_accepted_dir=accepted_dir,
        ),
        settings=settings,
        job_id=pass_row.current_job_id or "",
        progress=NullProgress(),
    )
    Pass3().build_accepted_output(ctx, {}, corrections)

    art_id = str(uuid.uuid4())
    db.add(Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass3",
        artifact_role=ArtifactRole.accepted.value,
        artifact_type="json",
        path=str(accepted_dir / "ball_color_polygons.json"),
    ))
    pass_row.state = PassState.accepted.value
    pass_row.is_dirty = False
    pass_row.latest_accepted_artifact_id = art_id
    pass_row.updated_at = _utcnow()

    project.status = ProjectStatus.in_progress.value
    project.updated_at = _utcnow()

    db.add(Event(
        project_id=project_id,
        event_type="pass_accepted",
        payload_json=json.dumps({"pass_name": "pass3"}),
    ))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Pass 2 — accepted patch image access (used by Pass 4 review page on hover)
# ---------------------------------------------------------------------------

@router.get("/{project_id}/passes/pass2/accepted/patches/{filename}")
def get_pass2_accepted_patch(
    project_id: str,
    filename: str,
    settings=Depends(get_settings),
):
    from pbva_core import paths as p
    patches_dir = p.pass_accepted_dir(settings.data_root, project_id, "pass2") / "patches" / "raw"
    path = (patches_dir / filename).resolve()
    try:
        path.relative_to(patches_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Patch not found")
    return FileResponse(str(path), media_type="image/png")


# ---------------------------------------------------------------------------
# Pass 4 — Ball Detection: raw file access, pause / resume, accept
# ---------------------------------------------------------------------------

_PASS4_MIME = {"json": "application/json", "png": "image/png"}


@router.get("/{project_id}/passes/pass4/raw/{filename}")
def get_pass4_raw_file(
    project_id: str,
    filename: str,
    settings=Depends(get_settings),
):
    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass4")
    path = (raw_dir / filename).resolve()
    try:
        path.relative_to(raw_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    suffix = path.suffix.lstrip(".")
    if suffix == "json":
        return JSONResponse(content=json.loads(path.read_text()))
    return FileResponse(str(path), media_type=_PASS4_MIME.get(suffix, "application/octet-stream"))

@router.get("/{project_id}/passes/pass4/patches")
def list_pass4_patches(
    project_id: str,
    settings=Depends(get_settings),
):
    """Return sorted list of frame numbers that have a mask patch PNG."""
    from pbva_core import paths as p
    patches_dir = p.pass_raw_dir(settings.data_root, project_id, "pass4") / "patches"
    if not patches_dir.exists():
        return {"frames": []}
    frames = sorted(int(f.stem) for f in patches_dir.glob("*.png"))
    return {"frames": frames}


@router.get("/{project_id}/passes/pass4/patches/{filename}")
def get_pass4_patch(
    project_id: str,
    filename: str,
    settings=Depends(get_settings),
):
    """Serve a single mask patch PNG from pass4/raw/patches/."""
    from pbva_core import paths as p
    patches_dir = p.pass_raw_dir(settings.data_root, project_id, "pass4") / "patches"
    path = (patches_dir / filename).resolve()
    try:
        path.relative_to(patches_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Patch not found")
    return FileResponse(str(path), media_type="image/png")


def _pass4_pause_file(data_root, project_id: str) -> Path:
    from pbva_core import paths as p
    return p.pass_raw_dir(data_root, project_id, "pass4") / ".pause"


@router.post("/{project_id}/passes/pass4/pause")
def pause_pass4(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    _get_pass_or_404(db, project_id, "pass4")
    pf = _pass4_pause_file(settings.data_root, project_id)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.touch()
    return {"ok": True}


@router.post("/{project_id}/passes/pass4/resume")
def resume_pass4(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    _get_pass_or_404(db, project_id, "pass4")
    pf = _pass4_pause_file(settings.data_root, project_id)
    pf.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/{project_id}/passes/pass4/accept")
def accept_pass4(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass4")
    if pass_row.state != PassState.waiting_for_user.value:
        raise HTTPException(status_code=409, detail=f"Pass 4 is in state '{pass_row.state}', cannot accept")

    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass4")
    accepted_dir = p.pass_accepted_dir(settings.data_root, project_id, "pass4")
    accepted_dir.mkdir(parents=True, exist_ok=True)

    import shutil
    for fname in ("detections.json", "detections_map.png"):
        src = raw_dir / fname
        if src.exists():
            shutil.copy2(src, accepted_dir / fname)

    art_id = str(uuid.uuid4())
    db.add(Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass4",
        artifact_role=ArtifactRole.accepted.value,
        artifact_type="json",
        path=str(accepted_dir / "detections.json"),
    ))
    pass_row.state = PassState.accepted.value
    pass_row.is_dirty = False
    pass_row.latest_accepted_artifact_id = art_id
    pass_row.updated_at = _utcnow()

    project = db.get(Project, project_id)
    project.status = ProjectStatus.in_progress.value
    project.updated_at = _utcnow()

    db.add(Event(
        project_id=project_id,
        event_type="pass_accepted",
        payload_json=json.dumps({"pass_name": "pass4"}),
    ))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Pass 5 — Segment Building: raw file access, accept
# ---------------------------------------------------------------------------


@router.get("/{project_id}/passes/pass5/raw/{filename}")
def get_pass5_raw_file(
    project_id: str,
    filename: str,
    settings=Depends(get_settings),
):
    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass5")
    path = (raw_dir / filename).resolve()
    try:
        path.relative_to(raw_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return JSONResponse(content=json.loads(path.read_text()))


@router.put("/{project_id}/passes/pass5/corrections")
def save_pass5_corrections(
    project_id: str,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    from pbva_core import paths as p
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass5")
    corrections_dir.mkdir(parents=True, exist_ok=True)
    (corrections_dir / "corrections.json").write_text(json.dumps(body, indent=2))

    _mark_settings_dirty(db, project_id, "pass5")
    db.commit()
    return {"ok": True}


@router.post("/{project_id}/passes/pass5/accept")
def accept_pass5(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass5")
    if pass_row.state not in (PassState.waiting_for_user.value, PassState.accepted.value):
        raise HTTPException(status_code=409, detail=f"Pass 5 is in state '{pass_row.state}', cannot accept")

    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass5")
    corrections_dir = p.pass_corrections_dir(settings.data_root, project_id, "pass5")
    accepted_dir = p.pass_accepted_dir(settings.data_root, project_id, "pass5")
    accepted_dir.mkdir(parents=True, exist_ok=True)

    src = raw_dir / "segments.json"
    if src.exists():
        raw_data = json.loads(src.read_text())
        corrections_path = corrections_dir / "corrections.json"
        if corrections_path.exists():
            deleted_ids = set(json.loads(corrections_path.read_text()).get("deleted_segment_ids", []))
            raw_data["segments"] = [s for s in raw_data["segments"] if s["id"] not in deleted_ids]
            raw_data["segment_count"] = len(raw_data["segments"])
        (accepted_dir / "segments.json").write_text(json.dumps(raw_data, indent=2))

    art_id = str(uuid.uuid4())
    db.add(Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass5",
        artifact_role=ArtifactRole.accepted.value,
        artifact_type="json",
        path=str(accepted_dir / "segments.json"),
    ))
    pass_row.state = PassState.accepted.value
    pass_row.is_dirty = False
    pass_row.latest_accepted_artifact_id = art_id
    pass_row.updated_at = _utcnow()

    project = db.get(Project, project_id)
    project.status = ProjectStatus.in_progress.value
    project.updated_at = _utcnow()

    db.add(Event(
        project_id=project_id,
        event_type="pass_accepted",
        payload_json=json.dumps({"pass_name": "pass5"}),
    ))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Pass 6 — Video Export: raw file access, accept
# ---------------------------------------------------------------------------


@router.get("/{project_id}/passes/pass6/raw/{filename}")
def get_pass6_raw_file(
    project_id: str,
    filename: str,
    settings=Depends(get_settings),
):
    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass6")
    path = (raw_dir / filename).resolve()
    try:
        path.relative_to(raw_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    suffix = path.suffix.lstrip(".")
    if suffix == "json":
        return JSONResponse(content=json.loads(path.read_text()))
    if suffix == "mp4":
        from fastapi.responses import FileResponse as _FR
        return _FR(str(path), media_type="video/mp4", filename=filename)
    return FileResponse(str(path), media_type="application/octet-stream")


@router.post("/{project_id}/passes/pass6/accept")
def accept_pass6(
    project_id: str,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
):
    pass_row = _get_pass_or_404(db, project_id, "pass6")
    if pass_row.state != PassState.waiting_for_user.value:
        raise HTTPException(status_code=409, detail=f"Pass 6 is in state '{pass_row.state}', cannot accept")

    from pbva_core import paths as p
    raw_dir = p.pass_raw_dir(settings.data_root, project_id, "pass6")
    accepted_dir = p.pass_accepted_dir(settings.data_root, project_id, "pass6")
    accepted_dir.mkdir(parents=True, exist_ok=True)

    raw_result_path = raw_dir / "result.json"
    if not raw_result_path.exists():
        raise HTTPException(status_code=500, detail="Pass 6 result.json not found")

    from pbva_core.types import Pass6RawResult
    raw_result = Pass6RawResult.model_validate_json(raw_result_path.read_text())

    from pbva_pipeline.pass6.run import Pass6
    from pbva_pipeline.base import NullProgress, PassPaths, PassContext

    project = db.get(Project, project_id)
    ctx = PassContext(
        project_id=project_id,
        project_name=project.name,
        video_path=p.project_root(settings.data_root, project_id) / "uploads" / "original.mp4",
        video_duration_s=project.video_duration_s or 0.0,
        video_fps=project.video_fps or 30.0,
        video_width=project.video_width or 1920,
        video_height=project.video_height or 1080,
        paths=PassPaths(
            project_root=p.project_root(settings.data_root, project_id),
            uploads_dir=p.uploads_dir(settings.data_root, project_id),
            derived_dir=p.derived_dir(settings.data_root, project_id),
            pass_raw_dir=raw_dir,
            pass_corrections_dir=p.pass_corrections_dir(settings.data_root, project_id, "pass6"),
            pass_accepted_dir=accepted_dir,
        ),
        settings=settings,
        job_id=pass_row.current_job_id or "",
        progress=NullProgress(),
    )
    accepted = Pass6().build_accepted_output(ctx, raw_result, None)

    art_id = str(uuid.uuid4())
    db.add(Artifact(
        id=art_id,
        project_id=project_id,
        pass_name="pass6",
        artifact_role=ArtifactRole.accepted.value,
        artifact_type="mp4",
        path=str(accepted_dir / "export.mp4"),
    ))
    pass_row.state = PassState.accepted.value
    pass_row.is_dirty = False
    pass_row.latest_accepted_artifact_id = art_id
    pass_row.updated_at = _utcnow()

    project.status = ProjectStatus.in_progress.value
    project.updated_at = _utcnow()

    db.add(Event(
        project_id=project_id,
        event_type="pass_accepted",
        payload_json=json.dumps({"pass_name": "pass6"}),
    ))
    db.commit()
    return {"ok": True, "data": accepted.model_dump(mode="json")}
