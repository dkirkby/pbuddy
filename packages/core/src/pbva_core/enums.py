"""Domain enumerations for project, pass, job, and artifact lifecycle states."""

from __future__ import annotations

from enum import Enum


class ProjectStatus(str, Enum):
    created = "created"
    video_ready = "video_ready"
    pass1_ready = "pass1_ready"
    pass1_waiting_for_review = "pass1_waiting_for_review"
    pass1_accepted = "pass1_accepted"
    pass2_waiting_for_review = "pass2_waiting_for_review"
    pass2_accepted = "pass2_accepted"
    pass3_waiting_for_review = "pass3_waiting_for_review"
    pass3_accepted = "pass3_accepted"
    pass4_waiting_for_review = "pass4_waiting_for_review"
    pass4_accepted = "pass4_accepted"
    pass5_waiting_for_review = "pass5_waiting_for_review"
    pass5_accepted = "pass5_accepted"
    replay_ready = "replay_ready"


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
