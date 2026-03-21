"""Database engine and session factory."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from pbva_db.models import Base


def get_engine(db_path: Path | str) -> Engine:
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{db_path}"
    else:
        url = f"sqlite:///{db_path}" if db_path != ":memory:" else "sqlite://"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    # Enable WAL mode for better concurrent read/write between API and worker.
    @event.listens_for(engine, "connect")
    def set_wal(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables if they don't exist (used instead of Alembic for simplicity)."""
    Base.metadata.create_all(engine)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)
