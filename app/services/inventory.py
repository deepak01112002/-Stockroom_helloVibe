from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Hold, HoldStatus, Order, Product, StockLevel, Warehouse


class InventoryError(RuntimeError):
    code = "inventory_error"


class InventoryNotFoundError(InventoryError):
    code = "inventory_not_found"


class InsufficientInventoryError(InventoryError):
    code = "insufficient_inventory"


class IdempotencyConflictError(InventoryError):
    code = "idempotency_conflict"


class HoldStateError(InventoryError):
    code = "invalid_hold_state"


class HoldExpiredError(InventoryError):
    code = "hold_expired"


class InventoryService:
    def __init__(self, session: Session, hold_ttl_minutes: int) -> None:
        self._session = session
        self._hold_ttl = timedelta(minutes=hold_ttl_minutes)

    def create_hold(
        self,
        *,
        product_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        quantity: int,
        client_id: str,
        idempotency_key: str,
    ) -> Hold:
        request_hash = self._request_hash(product_id, warehouse_id, quantity)
        try:
            with self._session.begin():
                existing = self._find_idempotent_hold(client_id, idempotency_key)
                if existing is not None:
                    self._assert_matching_request(existing, request_hash)
                    return existing

                stock_level = self._lock_stock_level(product_id, warehouse_id)
                available = stock_level.on_hand - stock_level.held - stock_level.sold
                if quantity > available:
                    raise InsufficientInventoryError(
                        f"requested {quantity} units but only {available} are available"
                    )

                stock_level.held += quantity
                hold = Hold(
                    product_id=product_id,
                    warehouse_id=warehouse_id,
                    quantity=quantity,
                    client_id=client_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    status=HoldStatus.ACTIVE,
                    expires_at=datetime.now(UTC) + self._hold_ttl,
                )
                self._session.add(hold)
                self._session.flush()
                return hold
        except IntegrityError:
            existing = self._find_idempotent_hold(client_id, idempotency_key)
            if existing is None:
                raise
            self._assert_matching_request(existing, request_hash)
            return existing

    def confirm_hold(self, hold_id: uuid.UUID) -> Order:
        with self._session.begin():
            hold = self._lock_hold(hold_id)
            if hold.status is HoldStatus.CONFIRMED:
                order = self._session.scalar(select(Order).where(Order.hold_id == hold.id))
                if order is None:
                    raise InventoryError("confirmed hold has no order")
                return order
            if hold.status is not HoldStatus.ACTIVE:
                raise HoldStateError(f"hold is {hold.status.value} and cannot be confirmed")
            if self._is_expired(hold):
                raise HoldExpiredError("hold has expired and awaits release by the expiry worker")

            stock_level = self._lock_stock_level(hold.product_id, hold.warehouse_id)
            if stock_level.held < hold.quantity:
                raise InventoryError("stock level held quantity is inconsistent with the hold")
            stock_level.held -= hold.quantity
            stock_level.sold += hold.quantity
            hold.status = HoldStatus.CONFIRMED
            hold.finalized_at = datetime.now(UTC)
            order = Order(
                hold_id=hold.id,
                product_id=hold.product_id,
                warehouse_id=hold.warehouse_id,
                quantity=hold.quantity,
            )
            self._session.add(order)
            self._session.flush()
            return order

    def cancel_hold(self, hold_id: uuid.UUID) -> Hold:
        with self._session.begin():
            hold = self._lock_hold(hold_id)
            if hold.status is HoldStatus.CANCELLED:
                return hold
            if hold.status is not HoldStatus.ACTIVE:
                raise HoldStateError(f"hold is {hold.status.value} and cannot be cancelled")

            stock_level = self._lock_stock_level(hold.product_id, hold.warehouse_id)
            if stock_level.held < hold.quantity:
                raise InventoryError("stock level held quantity is inconsistent with the hold")
            stock_level.held -= hold.quantity
            hold.status = HoldStatus.CANCELLED
            hold.finalized_at = datetime.now(UTC)
            self._session.flush()
            return hold

    def _lock_stock_level(self, product_id: uuid.UUID, warehouse_id: uuid.UUID) -> StockLevel:
        stock_level = self._session.scalar(
            select(StockLevel)
            .join(Product, Product.id == StockLevel.product_id)
            .join(Warehouse, Warehouse.id == StockLevel.warehouse_id)
            .where(
                StockLevel.product_id == product_id,
                StockLevel.warehouse_id == warehouse_id,
                Product.active.is_(True),
                Warehouse.active.is_(True),
            )
            .with_for_update()
        )
        if stock_level is None:
            raise InventoryNotFoundError("active product stock level was not found")
        return stock_level

    def _lock_hold(self, hold_id: uuid.UUID) -> Hold:
        hold = self._session.scalar(select(Hold).where(Hold.id == hold_id).with_for_update())
        if hold is None:
            raise InventoryNotFoundError("hold was not found")
        return hold

    def _find_idempotent_hold(self, client_id: str, idempotency_key: str) -> Hold | None:
        return self._session.scalar(
            select(Hold).where(
                Hold.client_id == client_id,
                Hold.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _request_hash(product_id: uuid.UUID, warehouse_id: uuid.UUID, quantity: int) -> str:
        payload = json.dumps(
            {
                "product_id": str(product_id),
                "quantity": quantity,
                "warehouse_id": str(warehouse_id),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _assert_matching_request(hold: Hold, request_hash: str) -> None:
        if hold.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency-Key was previously used with a different request payload"
            )

    @staticmethod
    def _is_expired(hold: Hold) -> bool:
        expires_at = hold.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= datetime.now(UTC)
