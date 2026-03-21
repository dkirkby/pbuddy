"""Domain error hierarchy."""

from __future__ import annotations


class PBuddyError(Exception):
    """Base class for all PBuddy errors."""


class ProjectNotFound(PBuddyError):
    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project not found: {project_id}")
        self.project_id = project_id


class PassNotReady(PBuddyError):
    def __init__(self, pass_name: str, current_state: str) -> None:
        super().__init__(f"Pass {pass_name} is not ready (current state: {current_state})")
        self.pass_name = pass_name
        self.current_state = current_state


class ArtifactMissing(PBuddyError):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(f"Artifact not found: {artifact_id}")
        self.artifact_id = artifact_id


class InvalidCorrection(PBuddyError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Invalid correction payload: {detail}")


class WorkerCancelled(PBuddyError):
    """Raised inside a pass when the job has been cancel-requested."""
