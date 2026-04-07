"""Unit tests for the DB models using an in-memory SQLite database."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from pbva_db.engine import get_engine, get_session_factory, init_db
from pbva_db.models import Artifact, Event, Job, Pass, Project


@pytest.fixture
def session():
    engine = get_engine(":memory:")
    from pbva_db.models import Base
    Base.metadata.create_all(engine)
    factory = get_session_factory(engine)
    with factory() as s:
        yield s


def make_project(session) -> Project:
    p = Project(
        id=str(uuid.uuid4()),
        name="Test Project",
        status="created",
        root_path="/tmp/test",
    )
    session.add(p)
    session.flush()
    return p


def test_create_and_retrieve_project(session):
    p = make_project(session)
    session.commit()
    result = session.get(Project, p.id)
    assert result is not None
    assert result.name == "Test Project"
    assert result.status == "created"


def test_create_pass_row(session):
    p = make_project(session)
    pass_row = Pass(
        id=str(uuid.uuid4()),
        project_id=p.id,
        pass_name="pass1",
        state="not_started",
    )
    session.add(pass_row)
    session.commit()
    result = session.get(Pass, pass_row.id)
    assert result.pass_name == "pass1"
    assert result.state == "not_started"


def test_create_job_row(session):
    p = make_project(session)
    job = Job(
        id=str(uuid.uuid4()),
        project_id=p.id,
        pass_name="pass1",
        job_type="run_pass",
        status="queued",
    )
    session.add(job)
    session.commit()
    result = session.get(Job, job.id)
    assert result.status == "queued"


def test_create_artifact_row(session):
    p = make_project(session)
    art = Artifact(
        id=str(uuid.uuid4()),
        project_id=p.id,
        pass_name="pass1",
        artifact_role="raw",
        artifact_type="png",
        path="/tmp/test/median_background_0.png",
    )
    session.add(art)
    session.commit()
    result = session.get(Artifact, art.id)
    assert result.artifact_type == "png"
    assert result.artifact_role == "raw"


def test_create_event_row(session):
    p = make_project(session)
    ev = Event(
        project_id=p.id,
        event_type="pass_waiting_for_user",
        payload_json='{"pass_name": "pass1"}',
    )
    session.add(ev)
    session.commit()
    assert ev.id is not None


def test_cascade_delete(session):
    p = make_project(session)
    pass_row = Pass(id=str(uuid.uuid4()), project_id=p.id, pass_name="pass1", state="not_started")
    session.add(pass_row)
    session.commit()

    session.delete(p)
    session.commit()
    assert session.get(Pass, pass_row.id) is None
