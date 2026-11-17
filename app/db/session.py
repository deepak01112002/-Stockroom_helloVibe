from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    """Yield one database session per request and always close it afterwards."""

    with SessionLocal() as session:
        yield session
