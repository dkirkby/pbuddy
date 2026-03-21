"""FastAPI application factory with WebSocket event broadcaster."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pbva_api.dependencies import get_settings
from pbva_api.websocket_manager import manager
from pbva_api.routes import health, projects, passes, artifacts, jobs

logger = logging.getLogger(__name__)

# Track the last event ID we've broadcast per project.
_event_watermarks: dict[str, int] = {}
_worker_process: subprocess.Popen | None = None


async def _poll_events(session_factory):
    """Background task: poll DB events table and broadcast over WebSocket."""
    while True:
        try:
            with session_factory() as session:
                from sqlalchemy import select
                from pbva_db.models import Event
                for project_id, last_id in list(_event_watermarks.items()):
                    new_events = session.execute(
                        select(Event)
                        .where(Event.project_id == project_id)
                        .where(Event.id > last_id)
                        .order_by(Event.id)
                    ).scalars().all()
                    for ev in new_events:
                        try:
                            payload = json.loads(ev.payload_json)
                        except Exception:
                            payload = {}
                        msg = {
                            "type": ev.event_type,
                            "project_id": ev.project_id,
                            "job_id": ev.job_id,
                            "payload": payload,
                        }
                        await manager.broadcast(project_id, msg)
                        _event_watermarks[project_id] = ev.id
        except Exception:
            logger.exception("Error in event polling loop")
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Ensure DB and tables exist.
    from pbva_db.engine import get_engine, init_db, get_session_factory
    engine = get_engine(settings.db_path)
    init_db(engine)
    session_factory = get_session_factory(engine)

    # Share session_factory on the app state.
    app.state.session_factory = session_factory

    # Start event-polling background task.
    task = asyncio.create_task(_poll_events(session_factory))

    # Launch worker subprocess.
    global _worker_process
    try:
        _worker_process = subprocess.Popen(
            [sys.executable, "-m", "pbva_worker.main"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        logger.info("Started worker process pid=%d", _worker_process.pid)
    except Exception as e:
        logger.warning("Could not start worker: %s", e)

    yield

    task.cancel()
    if _worker_process:
        _worker_process.terminate()
        try:
            _worker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _worker_process.kill()


def create_app() -> FastAPI:
    app = FastAPI(title="PBuddy API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(passes.router)
    app.include_router(artifacts.router)
    app.include_router(jobs.router)

    @app.websocket("/ws/projects/{project_id}")
    async def ws_endpoint(project_id: str, websocket: WebSocket):
        await manager.connect(project_id, websocket)
        # Register watermark starting from latest event.
        if project_id not in _event_watermarks:
            try:
                from sqlalchemy import select, func
                from pbva_db.models import Event
                with websocket.app.state.session_factory() as session:
                    max_id = session.execute(
                        select(func.max(Event.id)).where(Event.project_id == project_id)
                    ).scalar() or 0
                    _event_watermarks[project_id] = max_id
            except Exception:
                _event_watermarks[project_id] = 0
        try:
            while True:
                await websocket.receive_text()  # keep-alive; client may ping
        except WebSocketDisconnect:
            manager.disconnect(project_id, websocket)

    # Serve frontend static files if built.
    frontend_dist = Path(__file__).parent.parent.parent.parent.parent / "apps" / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


app = create_app()
