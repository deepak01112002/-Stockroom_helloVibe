"""Seed warehouses and a usable catalogue during container startup."""

from __future__ import annotations

from collections.abc import Iterator

import structlog
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.supplier_sync import DummyJsonClient, SupplierSyncService

logger = structlog.get_logger(__name__)


class FallbackProductSource:
    """Deterministic offline catalogue used only when DummyJSON is unavailable."""

    def iter_products(self) -> Iterator[dict[str, object]]:
        yield {
            "id": 999001,
            "title": "Stockroom Demo Desk",
            "description": "Fallback product for an offline local startup.",
            "category": "furniture",
            "price": "99.00",
            "stock": 20,
        }


def main() -> None:
    settings = get_settings()
    with SessionLocal() as session:
        source = DummyJsonClient(
            base_url=settings.supplier_base_url,
            timeout_seconds=settings.supplier_timeout_seconds,
            max_retries=settings.supplier_max_retries,
            page_size=settings.supplier_page_size,
        )
        sync_run = SupplierSyncService(session, source).run()
        if sync_run.status == "failed":
            session.rollback()
            sync_run = SupplierSyncService(session, FallbackProductSource()).run()
            logger.warning("supplier_bootstrap_failed_using_fallback")
        session.commit()
        logger.info(
            "catalogue_bootstrapped",
            status=sync_run.status,
            products_seen=sync_run.products_seen,
            errors_count=sync_run.errors_count,
        )


if __name__ == "__main__":
    main()
