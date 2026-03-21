"""Pass execution contract (Protocol) and supporting types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pbva_core.config import Settings
from pbva_core.errors import WorkerCancelled


@dataclass
class PassPaths:
    """Filesystem paths for one pass of one project."""

    project_root: Path
    uploads_dir: Path
    derived_dir: Path
    pass_raw_dir: Path
    pass_corrections_dir: Path
    pass_accepted_dir: Path

    @property
    def original_video(self) -> Path:
        return self.uploads_dir / "original.mp4"


class ProgressReporter(Protocol):
    def update(self, fraction: float, stage: str, message: str = "") -> None: ...

    def check_cancelled(self) -> None:
        """Raise WorkerCancelled if the job has been cancel-requested."""
        ...


@dataclass
class NullProgress:
    """No-op progress reporter for tests and direct script use."""

    def update(self, fraction: float, stage: str, message: str = "") -> None:
        pass

    def check_cancelled(self) -> None:
        pass


@dataclass
class LoggingProgress:
    """Progress reporter that prints to stdout."""

    def update(self, fraction: float, stage: str, message: str = "") -> None:
        pct = int(fraction * 100)
        print(f"  [{pct:3d}%] {stage}: {message}")

    def check_cancelled(self) -> None:
        pass


@dataclass
class PassContext:
    """Everything a pass needs to do its work."""

    project_id: str
    project_name: str
    video_path: Path
    video_duration_s: float
    video_fps: float
    video_width: int
    video_height: int
    paths: PassPaths
    settings: Settings
    job_id: str
    progress: Any  # ProgressReporter
    prior_accepted: dict[str, Any] = field(default_factory=dict)
    session_factory: Any = None  # sessionmaker | None
