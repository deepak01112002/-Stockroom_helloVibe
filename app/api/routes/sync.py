from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.schemas.supplier import SyncRunResponse
from app.services.supplier_sync import DummyJsonClient, SupplierSyncService

router = APIRouter(prefix="/sync", tags=["supplier"])
DatabaseSession = Annotated[Session, Depends(get_db_session)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]


@router.post("/products", response_model=SyncRunResponse, status_code=status.HTTP_200_OK)
def sync_products(session: DatabaseSession, settings: ApplicationSettings) -> SyncRunResponse:
    """Synchronize catalogue products from DummyJSON without changing stock levels."""

    source = DummyJsonClient(
        base_url=settings.supplier_base_url,
        timeout_seconds=settings.supplier_timeout_seconds,
        max_retries=settings.supplier_max_retries,
        page_size=settings.supplier_page_size,
    )
    sync_run = SupplierSyncService(session, source).run()
    session.commit()
    return SyncRunResponse(
        id=str(sync_run.id),
        status=sync_run.status,
        products_seen=sync_run.products_seen,
        products_created=sync_run.products_created,
        products_updated=sync_run.products_updated,
        errors_count=sync_run.errors_count,
        error_summary=sync_run.error_summary,
    )
