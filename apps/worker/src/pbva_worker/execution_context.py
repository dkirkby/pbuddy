"""Build a PassContext from a Job row and Settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pbva_core.config import Settings
from pbva_core import paths as p
from pbva_pipeline.base import PassContext, PassPaths

# For each pass, the name of the immediately prior pass whose accepted result.json
# should be loaded into ctx.prior_accepted.
_PRIOR_PASS: dict[str, str] = {
    "pass1": "pass0",
    "pass2": "pass1",
    "pass3": "pass2",
    "pass4": "pass3",
}


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

    # Load prior accepted output for passes that depend on a previous pass.
    prior_accepted: dict[str, Any] = {}
    prior_pass = _PRIOR_PASS.get(job.pass_name)
    if prior_pass:
        prior_accepted_path = (
            p.pass_accepted_dir(data_root, project.id, prior_pass) / "result.json"
        )
        if prior_accepted_path.exists():
            prior_accepted = json.loads(prior_accepted_path.read_text())

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
        prior_accepted=prior_accepted,
        session_factory=session_factory,
    )
