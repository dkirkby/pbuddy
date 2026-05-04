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
    is_dirty: bool = False
    runnable: bool = True
    current_job_id: str | None = None
    last_run_duration_s: float | None = None
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
# Pass 0 domain types
# ---------------------------------------------------------------------------


class Pass0RawResult(BaseModel):
    bg_width: int
    bg_height: int
    median_count: int = 0
    midpoint_chunk: int = 0
    video_fps: float = 30.0


class Pass0CorrectionPayload(BaseModel):
    court_geometry: CourtGeometry | None = None
    k1: float | None = None


class Pass0AcceptedOutput(BaseModel):
    court_geometry: CourtGeometry
    k1: float = 0.0
    bg_width: int
    bg_height: int


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


class Pass1SamplePoint(BaseModel):
    sx: float   # distorted image coords of baseline interior point
    sy: float
    px1: float  # distorted image coords of +perp_seg_length_px displacement
    py1: float
    px2: float  # distorted image coords of -perp_seg_length_px displacement
    py2: float


class Pass1CourtLine(BaseModel):
    name: str   # "near_baseline", "left_sideline", "right_sideline"
    color: str  # hex color for display, e.g. "#0ff"
    points: list[Pass1SamplePoint]


class Pass1ChunkProfiles(BaseModel):
    chunk_index: int
    vals: list[list[list[float]]]   # [line_idx][point_idx][sample_idx]


class Pass1SegmentAnalysis(BaseModel):
    reference: list[float]                      # gradient reference curve, shape (perp_seg_points,)
    lags: list[float | None]                    # per-chunk lag in sample units (None = not clean)
    similarities: list[float | None]            # per-chunk ZNCC similarity to reference
    positions: list[list[float] | None] = []   # per-chunk [x, y] image coord of the court line


class Pass1ChunkVertices(BaseModel):
    chunk_index: int
    baseline_left: list[float] | None = None    # undistorted [x, y] image coords
    baseline_right: list[float] | None = None
    baseline_center: list[float] | None = None
    kitchen_left: list[float] | None = None
    kitchen_right: list[float] | None = None
    kitchen_center: list[float] | None = None


class Pass1RawResult(BaseModel):
    bg_width: int
    bg_height: int
    midpoint_chunk_index: int
    perp_seg_length_px: float
    perp_seg_points: int
    k1: float = 0.0                             # radial distortion coefficient from Pass 0
    court_lines: list[Pass1CourtLine]           # geometry only, chunk-independent
    chunks: list[Pass1ChunkProfiles]            # per-chunk sampled values
    segment_analyses: list[list[Pass1SegmentAnalysis]] = []  # [line_idx][point_idx]
    chunk_vertices: list[Pass1ChunkVertices] = []            # per-chunk court line vertices


class Pass1CorrectionPayload(BaseModel):
    pass


class Pass1AcceptedOutput(BaseModel):
    bg_width: int
    bg_height: int


# ---------------------------------------------------------------------------
# Pass 2 domain types
# ---------------------------------------------------------------------------


class BallAnnotation(BaseModel):
    x: float        # ball centre x in bg-plate pixel space
    y: float        # ball centre y in bg-plate pixel space
    radius: float = 0.0  # annotated ball radius in bg-plate pixels (0 = not set)


class Pass2RawResult(BaseModel):
    fps: float
    bg_width: int
    bg_height: int


class Pass2CorrectionPayload(BaseModel):
    # frame_index (as string) -> ball centre + radius in bg-plate pixel space
    annotations: dict[str, BallAnnotation] = {}


class Pass2AcceptedOutput(BaseModel):
    fps: float
    bg_width: int
    bg_height: int
    annotation_count: int
    min_ball_radius: int
    max_ball_radius: int


# ---------------------------------------------------------------------------
# Pass 4 domain types
# ---------------------------------------------------------------------------


class Pass4AcceptedOutput(BaseModel):
    detection_count: int
    first_stable_frame: int
    last_stable_frame: int


# ---------------------------------------------------------------------------
# Pass 5 domain types
# ---------------------------------------------------------------------------


class Pass5RawResult(BaseModel):
    segment_count: int
    max_gap_frames: int
    large_gate_px: float
    small_gate_px: float
    min_segment_length: int


class Pass5AcceptedOutput(BaseModel):
    segment_count: int


# ---------------------------------------------------------------------------
# Pass 6 domain types
# ---------------------------------------------------------------------------


class Pass6RawResult(BaseModel):
    rally_count: int
    output_duration_s: float
    chapter_timestamps: str = ""


class Pass6AcceptedOutput(BaseModel):
    rally_count: int
    output_duration_s: float
    chapter_timestamps: str = ""
