"""Project creation, listing, detail, and video upload endpoints."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from pbva_core.config import Settings
from pbva_core.enums import PassState, ProjectStatus
from pbva_core import paths as p
from pbva_core.types import ProjectDetail, ProjectSummary, PassStatusSummary
from pbva_db.models import Pass, Project
from pbva_api.dependencies import get_db, get_settings

router = APIRouter(prefix="/api/projects", tags=["projects"])

PASS_NAMES = ["pass1", "pass2", "pass3", "pass4", "pass5"]


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _project_to_detail(project: Project) -> ProjectDetail:
    pass_statuses = [
        PassStatusSummary(
            pass_name=ps.pass_name,
            state=ps.state,
            current_job_id=ps.current_job_id,
            updated_at=ps.updated_at,
        )
        for ps in sorted(project.passes, key=lambda x: x.pass_name)
    ]
    return ProjectDetail(
        id=project.id,
        name=project.name,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        video_duration_s=project.video_duration_s,
        video_fps=project.video_fps,
        video_width=project.video_width,
        video_height=project.video_height,
        passes=pass_statuses,
    )


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(
    body: dict,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    project_id = str(uuid.uuid4())
    root = p.project_root(settings.data_root, project_id)

    project = Project(
        id=project_id,
        name=name,
        status=ProjectStatus.created.value,
        root_path=str(root),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(project)

    for pass_name in PASS_NAMES:
        pass_row = Pass(
            id=str(uuid.uuid4()),
            project_id=project_id,
            pass_name=pass_name,
            state=PassState.not_started.value,
            updated_at=_utcnow(),
        )
        db.add(pass_row)

    db.commit()
    db.refresh(project)

    # Create filesystem directories.
    p.ensure_project_dirs(settings.data_root, project_id)

    return _project_to_detail(project)


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db)):
    from sqlalchemy import select
    projects = db.execute(select(Project).order_by(Project.created_at.desc())).scalars().all()
    return [
        ProjectSummary(
            id=proj.id,
            name=proj.name,
            status=proj.status,
            created_at=proj.created_at,
            updated_at=proj.updated_at,
            video_duration_s=proj.video_duration_s,
            video_fps=proj.video_fps,
            video_width=proj.video_width,
            video_height=proj.video_height,
        )
        for proj in projects
    ]


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, db: Session = Depends(get_db)):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    project = db.execute(
        select(Project).where(Project.id == project_id).options(selectinload(Project.passes))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_to_detail(project)


@router.post("/{project_id}/video")
async def upload_video(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    project = db.execute(
        select(Project).where(Project.id == project_id).options(selectinload(Project.passes))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    dest = p.uploads_dir(settings.data_root, project_id) / "original.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Stream the upload to disk.
    with dest.open("wb") as f:
        while chunk := await file.read(1024 * 1024):  # 1 MB chunks
            f.write(chunk)

    # Probe video metadata.
    try:
        meta = _probe_video(dest, settings)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Video probe failed: {exc}")
    project.video_path = str(dest)
    project.video_duration_s = meta.get("duration_s")
    project.video_fps = meta.get("fps")
    project.video_width = meta.get("width")
    project.video_height = meta.get("height")
    project.status = ProjectStatus.pass1_ready.value
    project.updated_at = _utcnow()
    db.commit()
    db.refresh(project)

    return {"ok": True, "data": _project_to_detail(project)}


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    root = Path(project.root_path)
    db.delete(project)
    db.commit()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


@router.get("/{project_id}/video")
def serve_project_video(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    video_path = Path(project.video_path) if project.video_path else None
    if not video_path or not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    # Guard against directory traversal.
    try:
        video_path.resolve().relative_to(settings.data_root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(str(video_path), media_type="video/mp4")


@router.get("/{project_id}/video/metadata")
def video_metadata(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "ok": True,
        "data": {
            "duration_s": project.video_duration_s,
            "fps": project.video_fps,
            "width": project.video_width,
            "height": project.video_height,
        },
    }


def _probe_video(path: Path, settings: Settings) -> dict:
    """Return basic video metadata via ffprobe.

    Raises RuntimeError if ffprobe fails or produces no usable output.
    """
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        # Derive ffprobe path from the resolved ffmpeg location.
        ffmpeg_resolved = shutil.which(settings.ffmpeg_bin)
        if ffmpeg_resolved:
            ffprobe_bin = str(Path(ffmpeg_resolved).parent / "ffprobe")
    if not ffprobe_bin:
        raise RuntimeError(
            f"ffprobe not found on PATH and could not be derived from ffmpeg_bin={settings.ffmpeg_bin!r}; "
            "ensure ffmpeg is installed and on PATH"
        )
    cmd = [
        ffprobe_bin, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed (exit {result.returncode}): {result.stderr.strip()}")
    data = json.loads(result.stdout)
    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), {}
    )
    if not video_stream:
        raise RuntimeError("ffprobe found no video stream in the uploaded file")
    duration = float(data.get("format", {}).get("duration", 0))
    fps_str = video_stream.get("r_frame_rate", "30/1")
    num, den = fps_str.split("/")
    fps = float(num) / float(den)
    return {
        "duration_s": round(duration, 3),
        "fps": round(fps, 3),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
    }
