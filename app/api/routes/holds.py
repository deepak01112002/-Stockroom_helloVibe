from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.schemas.holds import CreateHoldRequest, HoldResponse, OrderResponse
from app.services.inventory import (
    InventoryError,
    InventoryNotFoundError,
    InventoryService,
)

router = APIRouter(prefix="/holds", tags=["holds"])
DatabaseSession = Annotated[Session, Depends(get_db_session)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]
ClientId = Annotated[str, Header(alias="X-Client-Id", min_length=1, max_length=128)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


@router.post("", response_model=HoldResponse, status_code=status.HTTP_201_CREATED)
def create_hold(
    payload: CreateHoldRequest,
    session: DatabaseSession,
    settings: ApplicationSettings,
    client_id: ClientId,
    idempotency_key: IdempotencyKey,
) -> HoldResponse:
    """Reserve stock in one warehouse for the configured hold duration."""

    try:
        hold = InventoryService(session, settings.hold_ttl_minutes).create_hold(
            product_id=payload.product_id,
            warehouse_id=payload.warehouse_id,
            quantity=payload.quantity,
            client_id=client_id,
            idempotency_key=idempotency_key,
        )
    except InventoryError as exc:
        _raise_inventory_error(exc)
    return HoldResponse.model_validate(hold)


@router.post("/{hold_id}/confirm", response_model=OrderResponse)
def confirm_hold(
    hold_id: uuid.UUID, session: DatabaseSession, settings: ApplicationSettings
) -> OrderResponse:
    """Convert an active unexpired hold into an order."""

    try:
        order = InventoryService(session, settings.hold_ttl_minutes).confirm_hold(hold_id)
    except InventoryError as exc:
        _raise_inventory_error(exc)
    return OrderResponse.model_validate(order)


@router.delete("/{hold_id}", response_model=HoldResponse)
def cancel_hold(
    hold_id: uuid.UUID, session: DatabaseSession, settings: ApplicationSettings
) -> HoldResponse:
    """Release an active hold's inventory; cancellation is safe to repeat."""

    try:
        hold = InventoryService(session, settings.hold_ttl_minutes).cancel_hold(hold_id)
    except InventoryError as exc:
        _raise_inventory_error(exc)
    return HoldResponse.model_validate(hold)


def _raise_inventory_error(exc: InventoryError) -> None:
    if isinstance(exc, InventoryNotFoundError):
        http_status = status.HTTP_404_NOT_FOUND
    else:
        http_status = status.HTTP_409_CONFLICT
    raise HTTPException(status_code=http_status, detail={"code": exc.code, "message": str(exc)})
