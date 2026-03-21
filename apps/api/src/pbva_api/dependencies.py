"""FastAPI dependency injection helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from pbva_core.config import Settings
from pbva_db.engine import get_engine, get_session_factory, init_db

_session_factory: sessionmaker | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_db_session_factory(settings: Settings = Depends(get_settings)) -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(settings.db_path)
        init_db(engine)
        _session_factory = get_session_factory(engine)
    return _session_factory


def get_db(factory: sessionmaker = Depends(get_db_session_factory)) -> Session:
    with factory() as session:
        yield session
