"""SQLAlchemy ORM models for all five core tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="created")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_duration_s: Mapped[float | None] = mapped_column(nullable=True)
    video_fps: Mapped[float | None] = mapped_column(nullable=True)
    video_width: Mapped[int | None] = mapped_column(nullable=True)
    video_height: Mapped[int | None] = mapped_column(nullable=True)

    passes: Mapped[list[Pass]] = relationship(back_populates="project", cascade="all, delete-orphan")
    jobs: Mapped[list[Job]] = relationship(back_populates="project", cascade="all, delete-orphan")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="project", cascade="all, delete-orphan")
    events: Mapped[list[Event]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Pass(Base):
    __tablename__ = "passes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    pass_name: Mapped[str] = mapped_column(String(16), nullable=False)  # pass1..pass4
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    current_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    latest_raw_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    latest_correction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    latest_accepted_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_run_duration_s: Mapped[float | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)

    project: Mapped[Project] = relationship(back_populates="passes")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    pass_name: Mapped[str] = mapped_column(String(16), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, default="run_pass")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(default=1)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="jobs")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    pass_name: Mapped[str | None] = mapped_column(String(16), nullable=True)
    artifact_role: Mapped[str] = mapped_column(String(16), nullable=False)  # ArtifactRole value
    artifact_type: Mapped[str] = mapped_column(String(16), nullable=False)  # json, png, mp4, etc.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="artifacts")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="events")
