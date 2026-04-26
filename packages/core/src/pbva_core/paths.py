"""Filesystem path helpers for project artifacts."""

from __future__ import annotations

from pathlib import Path


def project_root(data_root: Path, project_id: str) -> Path:
    return data_root / "projects" / project_id


def uploads_dir(data_root: Path, project_id: str) -> Path:
    return project_root(data_root, project_id) / "uploads"


def derived_dir(data_root: Path, project_id: str) -> Path:
    return project_root(data_root, project_id) / "derived"


def pass_dir(data_root: Path, project_id: str, pass_name: str) -> Path:
    return project_root(data_root, project_id) / "passes" / pass_name


def pass_raw_dir(data_root: Path, project_id: str, pass_name: str) -> Path:
    return pass_dir(data_root, project_id, pass_name) / "raw"


def pass_corrections_dir(data_root: Path, project_id: str, pass_name: str) -> Path:
    return pass_dir(data_root, project_id, pass_name) / "corrections"


def pass_accepted_dir(data_root: Path, project_id: str, pass_name: str) -> Path:
    return pass_dir(data_root, project_id, pass_name) / "accepted"


def ensure_project_dirs(data_root: Path, project_id: str) -> None:
    """Create the full directory tree for a new project."""
    for pass_name in ("pass0", "pass1", "pass2", "pass3", "pass4"):
        pass_raw_dir(data_root, project_id, pass_name).mkdir(parents=True, exist_ok=True)
        pass_corrections_dir(data_root, project_id, pass_name).mkdir(parents=True, exist_ok=True)
        pass_accepted_dir(data_root, project_id, pass_name).mkdir(parents=True, exist_ok=True)
    uploads_dir(data_root, project_id).mkdir(parents=True, exist_ok=True)
    derived_dir(data_root, project_id).mkdir(parents=True, exist_ok=True)
    (project_root(data_root, project_id) / "exports").mkdir(parents=True, exist_ok=True)
    (project_root(data_root, project_id) / "logs").mkdir(parents=True, exist_ok=True)
