from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.base import Base
from app.db.models import Hold, HoldStatus, Product, StockLevel, Warehouse
from app.services.inventory import InventoryService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def test_expiry_batch_releases_only_due_active_holds() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        product = Product(
            supplier_id="expiry-product",
            title="Expiry Product",
            description=None,
            category="test",
            price_cents=100,
        )
        warehouse = Warehouse(code="warehouse-a", name="Warehouse A")
        session.add_all([product, warehouse])
        session.flush()
        stock_level = StockLevel(
            product_id=product.id,
            warehouse_id=warehouse.id,
            on_hand=5,
            held=3,
        )
        due_hold = Hold(
            product_id=product.id,
            warehouse_id=warehouse.id,
            client_id="expiry-client",
            idempotency_key="due",
            request_hash="a" * 64,
            quantity=2,
            status=HoldStatus.ACTIVE,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        future_hold = Hold(
            product_id=product.id,
            warehouse_id=warehouse.id,
            client_id="expiry-client",
            idempotency_key="future",
            request_hash="b" * 64,
            quantity=1,
            status=HoldStatus.ACTIVE,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        session.add_all([stock_level, due_hold, future_hold])
        session.commit()

        expired = InventoryService(session, hold_ttl_minutes=15).expire_due_holds(batch_size=10)
        assert [hold.id for hold in expired] == [due_hold.id]
        assert due_hold.status is HoldStatus.EXPIRED
        assert due_hold.finalized_at is not None
        assert future_hold.status is HoldStatus.ACTIVE
        assert stock_level.held == 1

        assert InventoryService(session, hold_ttl_minutes=15).expire_due_holds(batch_size=10) == []
        assert stock_level.held == 1
