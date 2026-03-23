"""Pydantic schemas for API responses and artifact payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


class OkResponse(BaseModel):
    ok: bool = True
    data: Any = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class ProjectSummary(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    video_duration_s: float | None = None
    video_fps: float | None = None
    video_width: int | None = None
    video_height: int | None = None


class PassStatusSummary(BaseModel):
    pass_name: str
    state: str
    current_job_id: str | None = None
    updated_at: datetime


class ProjectDetail(ProjectSummary):
    passes: list[PassStatusSummary] = []


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class JobSummary(BaseModel):
    id: str
    project_id: str
    pass_name: str
    job_type: str
    status: str
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class ArtifactRef(BaseModel):
    id: str
    project_id: str
    pass_name: str | None = None
    artifact_role: str
    artifact_type: str
    path: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Pass 1 domain types
# ---------------------------------------------------------------------------


class StableBounds(BaseModel):
    in_time_s: float
    out_time_s: float


class CourtCorner(BaseModel):
    x: float  # pixel coordinates in the median background image
    y: float


class CourtGeometry(BaseModel):
    top_left: CourtCorner
    top_right: CourtCorner
    bottom_left: CourtCorner
    bottom_right: CourtCorner
    # Deprecated: net endpoints are now derived from the 4 corners via court proportions.
    # Kept optional for backward compatibility with stored data.
    net_left: CourtCorner | None = None
    net_right: CourtCorner | None = None


class Pass1RawResult(BaseModel):
    stable_bounds: StableBounds
    median_background_path: str   # relative to project root
    bg_width: int                 # actual pixel width of median_background.png
    bg_height: int                # actual pixel height of median_background.png


class Pass1CorrectionPayload(BaseModel):
    court_geometry: CourtGeometry | None = None


class Pass1AcceptedOutput(BaseModel):
    stable_bounds: StableBounds
    court_geometry: CourtGeometry
    median_background_artifact_id: str
    bg_width: int
    bg_height: int


# ---------------------------------------------------------------------------
# Pass 2 domain types
# ---------------------------------------------------------------------------


class BallAnnotation(BaseModel):
    x: float   # ball centre x in bg-plate pixel space
    y: float   # ball centre y in bg-plate pixel space


class Pass2RawResult(BaseModel):
    fps: float
    bg_width: int
    bg_height: int


class Pass2CorrectionPayload(BaseModel):
    # frame_index (as string) -> ball centre in bg-plate pixel space
    annotations: dict[str, BallAnnotation] = {}


class Pass2AcceptedOutput(BaseModel):
    fps: float
    bg_width: int
    bg_height: int
    annotation_count: int
