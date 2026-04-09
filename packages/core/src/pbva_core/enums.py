"""Domain enumerations for project, pass, job, and artifact lifecycle states."""

from __future__ import annotations

from enum import Enum


class ProjectStatus(str, Enum):
    created = "created"
    video_ready = "video_ready"
    in_progress = "in_progress"


class PassState(str, Enum):
    not_started = "not_started"
    queued = "queued"
    running = "running"
    produced_raw_output = "produced_raw_output"
    waiting_for_user = "waiting_for_user"
    accepted = "accepted"
    failed = "failed"
    cancelled = "cancelled"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    cancel_requested = "cancel_requested"


class ArtifactRole(str, Enum):
    raw = "raw"
    correction = "correction"
    accepted = "accepted"
    preview = "preview"
    export = "export"


class JobType(str, Enum):
    run_pass = "run_pass"
    rebuild_accepted_state = "rebuild_accepted_state"
    export = "export"
