from __future__ import annotations

import uuid
from collections.abc import Generator

from app.api.routes.holds import get_db_session
from app.db.base import Base
from app.db.models import Hold, HoldStatus, Product, StockLevel, Warehouse
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def test_hold_creation_idempotency_confirmation_and_cancellation() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        product = Product(
            supplier_id="hold-product",
            title="Holdable Desk",
            description=None,
            category="furniture",
            price_cents=1000,
        )
        warehouse = Warehouse(code="warehouse-a", name="Warehouse A")
        session.add_all([product, warehouse])
        session.flush()
        stock_level = StockLevel(product_id=product.id, warehouse_id=warehouse.id, on_hand=5)
        session.add(stock_level)
        session.commit()
        product_id, warehouse_id, stock_level_id = product.id, warehouse.id, stock_level.id

    def override_session() -> Generator[Session, None, None]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    headers = {"X-Client-Id": "shopper-1", "Idempotency-Key": "key-1"}
    payload = {"product_id": str(product_id), "warehouse_id": str(warehouse_id), "quantity": 2}
    app.dependency_overrides[get_db_session] = override_session
    try:
        client = TestClient(app)
        created = client.post("/holds", json=payload, headers=headers)
        replayed = client.post("/holds", json=payload, headers=headers)
        conflict = client.post("/holds", json={**payload, "quantity": 3}, headers=headers)
        order = client.post(f"/holds/{created.json()['id']}/confirm")
        replayed_order = client.post(f"/holds/{created.json()['id']}/confirm")
        second_hold = client.post(
            "/holds",
            json={**payload, "quantity": 1},
            headers={"X-Client-Id": "shopper-1", "Idempotency-Key": "key-2"},
        )
        cancelled = client.delete(f"/holds/{second_hold.json()['id']}")
        replayed_cancel = client.delete(f"/holds/{second_hold.json()['id']}")
        insufficient = client.post(
            "/holds",
            json={**payload, "quantity": 4},
            headers={"X-Client-Id": "shopper-2", "Idempotency-Key": "key-3"},
        )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 201
    assert replayed.status_code == 201
    assert replayed.json()["id"] == created.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert order.status_code == 200
    assert replayed_order.status_code == 200
    assert replayed_order.json()["id"] == order.json()["id"]
    assert cancelled.json()["status"] == "cancelled"
    assert replayed_cancel.json()["status"] == "cancelled"
    assert insufficient.status_code == 409
    assert insufficient.json()["error"]["code"] == "insufficient_inventory"

    with Session(engine) as session:
        stock_level = session.get(StockLevel, stock_level_id)
        first_hold = session.get(Hold, uuid.UUID(created.json()["id"]))
        assert stock_level.held == 0
        assert stock_level.sold == 2
        assert first_hold.status is HoldStatus.CONFIRMED
        assert session.scalar(select(Hold).where(Hold.status == HoldStatus.CANCELLED)) is not None
