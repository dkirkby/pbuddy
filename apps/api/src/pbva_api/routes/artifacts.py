"""Artifact serving endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from pbva_db.models import Artifact
from pbva_api.dependencies import get_db, get_settings

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "mp4": "video/mp4",
    "csv": "text/csv",
    "json": "application/json",
    "parquet": "application/octet-stream",
}


@router.get("/{artifact_id}")
def get_artifact(
    artifact_id: str,
    db=Depends(get_db),
    settings=Depends(get_settings),
):
    art = db.get(Artifact, artifact_id)
    if art is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    path = Path(art.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file not found on disk")

    # Directory traversal guard: path must be inside data_root.
    try:
        path.resolve().relative_to(settings.data_root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    mime = _MIME.get(art.artifact_type, "application/octet-stream")
    if art.artifact_type == "json":
        return JSONResponse(content=__import__("json").loads(path.read_text()))

    return FileResponse(str(path), media_type=mime)
