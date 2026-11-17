from __future__ import annotations

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.supplier_sync import DummyJsonClient, SupplierSyncService


def main() -> None:
    settings = get_settings()
    source = DummyJsonClient(
        base_url=settings.supplier_base_url,
        timeout_seconds=settings.supplier_timeout_seconds,
        max_retries=settings.supplier_max_retries,
        page_size=settings.supplier_page_size,
    )
    with SessionLocal() as session:
        sync_run = SupplierSyncService(session, source).run()
        session.commit()
    print(
        f"sync {sync_run.id}: {sync_run.status}; "
        f"created={sync_run.products_created}, updated={sync_run.products_updated}, "
        f"errors={sync_run.errors_count}"
    )


if __name__ == "__main__":
    main()
