"""Build a PassContext from a Job row and Settings."""

from __future__ import annotations

import json
from pathlib import Path

from pbva_core.config import Settings
from pbva_core import paths as p
from pbva_pipeline.base import PassContext, PassPaths


def build_pass_context(job, project, settings: Settings, session_factory, progress) -> PassContext:
    """Construct a PassContext for the given job + project."""
    data_root = settings.data_root
    project_root = p.project_root(data_root, project.id)

    pass_paths = PassPaths(
        project_root=project_root,
        uploads_dir=p.uploads_dir(data_root, project.id),
        derived_dir=p.derived_dir(data_root, project.id),
        pass_raw_dir=p.pass_raw_dir(data_root, project.id, job.pass_name),
        pass_corrections_dir=p.pass_corrections_dir(data_root, project.id, job.pass_name),
        pass_accepted_dir=p.pass_accepted_dir(data_root, project.id, job.pass_name),
    )

    return PassContext(
        project_id=project.id,
        project_name=project.name,
        video_path=Path(project.video_path) if project.video_path else pass_paths.original_video,
        video_duration_s=project.video_duration_s or 0.0,
        video_fps=project.video_fps or 30.0,
        video_width=project.video_width or 1920,
        video_height=project.video_height or 1080,
        paths=pass_paths,
        settings=settings,
        job_id=job.id,
        progress=progress,
        session_factory=session_factory,
    )
