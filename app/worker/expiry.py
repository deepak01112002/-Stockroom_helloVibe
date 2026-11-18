from __future__ import annotations

import time

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.inventory import InventoryService

logger = structlog.get_logger(__name__)


def run_once(
    session_factory: sessionmaker[Session] = SessionLocal,
    *,
    batch_size: int | None = None,
) -> int:
    """Expire one claimed batch and return the number of released holds."""

    settings = get_settings()
    with session_factory() as session:
        expired_holds = InventoryService(session, settings.hold_ttl_minutes).expire_due_holds(
            batch_size or settings.expiry_worker_batch_size
        )
    if expired_holds:
        logger.info("holds_expired", count=len(expired_holds))
    return len(expired_holds)


def run_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "expiry_worker_started",
        batch_size=settings.expiry_worker_batch_size,
        interval_seconds=settings.expiry_worker_interval_seconds,
    )
    while True:
        run_once(batch_size=settings.expiry_worker_batch_size)
        time.sleep(settings.expiry_worker_interval_seconds)


if __name__ == "__main__":
    run_forever()
