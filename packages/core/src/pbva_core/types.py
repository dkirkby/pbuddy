"""Pydantic schemas for API responses and artifact payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    net_left: CourtCorner
    net_right: CourtCorner


class BallColorModel(BaseModel):
    hsv_lower: list[float] = Field(..., min_length=3, max_length=3)
    hsv_upper: list[float] = Field(..., min_length=3, max_length=3)


class Pass1RawResult(BaseModel):
    stable_bounds: StableBounds
    court_geometry: CourtGeometry
    ball_color_model: BallColorModel
    median_background_path: str   # relative to project root
    court_overlay_path: str       # relative to project root
    confidence: dict[str, float]


class Pass1CorrectionPayload(BaseModel):
    stable_bounds: StableBounds | None = None
    court_geometry: CourtGeometry | None = None
    ball_color_model: BallColorModel | None = None


class Pass1AcceptedOutput(BaseModel):
    stable_bounds: StableBounds
    court_geometry: CourtGeometry
    ball_color_model: BallColorModel
    median_background_artifact_id: str
    calibration_confidence: float
