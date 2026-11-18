from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.db.models import Hold, HoldStatus, Order, PaymentEvent, Product, StockLevel
from app.services.inventory import InventoryService
from app.services.warehouses import ensure_seed_warehouses
from app.worker.expiry import run_once
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

API_BASE_URL = os.getenv("API_BASE_URL")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET")


@pytest.fixture
def lifecycle_stock() -> Iterator[tuple[sessionmaker[Session], uuid.UUID, uuid.UUID]]:
    if API_BASE_URL is None or TEST_DATABASE_URL is None:
        pytest.skip("requires API_BASE_URL and TEST_DATABASE_URL")

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    supplier_id = "postgres-lifecycle-test-product"
    with factory() as session, session.begin():
        warehouse = ensure_seed_warehouses(session)["warehouse-a"]
        product = session.scalar(select(Product).where(Product.supplier_id == supplier_id))
        if product is None:
            product = Product(
                supplier_id=supplier_id,
                title="PostgreSQL Lifecycle Test Product",
                description=None,
                category="test",
                price_cents=100,
                active=True,
            )
            session.add(product)
            session.flush()
        else:
            hold_ids = select(Hold.id).where(Hold.product_id == product.id)
            session.execute(delete(PaymentEvent).where(PaymentEvent.hold_id.in_(hold_ids)))
            session.execute(delete(Order).where(Order.product_id == product.id))
            session.execute(delete(Hold).where(Hold.product_id == product.id))
            session.execute(delete(StockLevel).where(StockLevel.product_id == product.id))
        session.add(StockLevel(product_id=product.id, warehouse_id=warehouse.id, on_hand=20))
        product_id, warehouse_id = product.id, warehouse.id

    try:
        yield factory, product_id, warehouse_id
    finally:
        with factory() as session, session.begin():
            product = session.scalar(select(Product).where(Product.supplier_id == supplier_id))
            if product is not None:
                hold_ids = select(Hold.id).where(Hold.product_id == product.id)
                session.execute(delete(PaymentEvent).where(PaymentEvent.hold_id.in_(hold_ids)))
                session.execute(delete(Order).where(Order.product_id == product.id))
                session.execute(delete(Hold).where(Hold.product_id == product.id))
                session.execute(delete(StockLevel).where(StockLevel.product_id == product.id))
                session.execute(delete(Product).where(Product.id == product.id))
        engine.dispose()


@pytest.mark.postgres
def test_expiry_workers_and_payment_webhook_lifecycle(
    lifecycle_stock: tuple[sessionmaker[Session], uuid.UUID, uuid.UUID],
) -> None:
    assert API_BASE_URL is not None
    assert WEBHOOK_SECRET is not None
    factory, product_id, warehouse_id = lifecycle_stock
    _wait_for_api()
    with factory() as session:
        inventory = InventoryService(session, hold_ttl_minutes=15)
        due_holds = [
            inventory.create_hold(
                product_id=product_id,
                warehouse_id=warehouse_id,
                quantity=1,
                client_id="lifecycle-client",
                idempotency_key=f"due-{index}",
            )
            for index in range(10)
        ]
        success_hold = inventory.create_hold(
            product_id=product_id,
            warehouse_id=warehouse_id,
            quantity=2,
            client_id="lifecycle-client",
            idempotency_key="success",
        )
        failed_hold = inventory.create_hold(
            product_id=product_id,
            warehouse_id=warehouse_id,
            quantity=1,
            client_id="lifecycle-client",
            idempotency_key="failed",
        )
        for hold in due_holds:
            hold.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        due_hold_id = due_holds[0].id
        success_hold_id, failed_hold_id = success_hold.id, failed_hold.id

    barrier = threading.Barrier(2)

    def expire_batch() -> int:
        barrier.wait(timeout=10)
        return run_once(factory, batch_size=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        expired_counts = list(executor.map(lambda _: expire_batch(), range(2)))
    assert sum(expired_counts) == 10

    success = _post_signed("event-success", success_hold_id, "succeeded")
    replay = _post_signed("event-success", success_hold_id, "succeeded")
    failed = _post_signed("event-failed", failed_hold_id, "failed")
    late = _post_signed("event-late", due_hold_id, "succeeded")
    assert success.json()["result"] == "confirmed"
    assert replay.json()["duplicate"] is True
    assert replay.json()["order_id"] == success.json()["order_id"]
    assert failed.json()["result"] == "cancelled"
    assert late.json()["result"] == "already_expired"

    with factory() as session:
        stock = session.scalar(
            select(StockLevel).where(
                StockLevel.product_id == product_id,
                StockLevel.warehouse_id == warehouse_id,
            )
        )
        expired_count = session.scalar(
            select(func.count())
            .select_from(Hold)
            .where(Hold.product_id == product_id, Hold.status == HoldStatus.EXPIRED)
        )
        hold_ids = select(Hold.id).where(Hold.product_id == product_id)
        event_count = session.scalar(
            select(func.count()).select_from(PaymentEvent).where(PaymentEvent.hold_id.in_(hold_ids))
        )
        order_count = session.scalar(
            select(func.count()).select_from(Order).where(Order.product_id == product_id)
        )
    assert stock is not None
    assert (stock.held, stock.sold) == (0, 2)
    assert stock.held + stock.sold <= stock.on_hand
    assert expired_count == 10
    assert event_count == 3
    assert order_count == 1


def _post_signed(event_id: str, hold_id: uuid.UUID, outcome: str) -> httpx.Response:
    assert API_BASE_URL is not None
    assert WEBHOOK_SECRET is not None
    body = (
        f'{{"provider_event_id":"{event_id}","hold_id":"{hold_id}","event":"payment.{outcome}"}}'
    ).encode()
    signature = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = httpx.post(
        f"{API_BASE_URL}/webhooks/payments",
        content=body,
        headers={"X-Webhook-Signature": signature},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return response


def _wait_for_api() -> None:
    assert API_BASE_URL is not None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{API_BASE_URL}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    pytest.fail("API did not become healthy before lifecycle testing")
