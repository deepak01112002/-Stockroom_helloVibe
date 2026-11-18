from __future__ import annotations

import os
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from app.db.models import Hold, HoldStatus, Order, Product, StockLevel
from app.services.warehouses import ensure_seed_warehouses
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

API_BASE_URL = os.getenv("API_BASE_URL")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
CONCURRENCY = 50


@pytest.fixture
def concurrency_stock() -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    if API_BASE_URL is None or TEST_DATABASE_URL is None:
        pytest.skip("requires API_BASE_URL and TEST_DATABASE_URL")

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    supplier_id = "postgres-concurrency-test-product"
    with Session(engine) as session:
        with session.begin():
            warehouse = ensure_seed_warehouses(session)["warehouse-a"]

            product = session.scalar(select(Product).where(Product.supplier_id == supplier_id))
            if product is None:
                product = Product(
                    supplier_id=supplier_id,
                    title="PostgreSQL Concurrency Test Product",
                    description=None,
                    category="test",
                    price_cents=100,
                    active=True,
                )
                session.add(product)
                session.flush()
            else:
                session.execute(delete(Order).where(Order.product_id == product.id))
                session.execute(delete(Hold).where(Hold.product_id == product.id))
                session.execute(delete(StockLevel).where(StockLevel.product_id == product.id))

            stock_level = StockLevel(
                product_id=product.id,
                warehouse_id=warehouse.id,
                on_hand=3,
                held=0,
                sold=0,
            )
            session.add(stock_level)
        product_id, warehouse_id = product.id, warehouse.id

    try:
        yield product_id, warehouse_id
    finally:
        with Session(engine) as session, session.begin():
            product = session.scalar(select(Product).where(Product.supplier_id == supplier_id))
            if product is not None:
                session.execute(delete(Order).where(Order.product_id == product.id))
                session.execute(delete(Hold).where(Hold.product_id == product.id))
                session.execute(delete(StockLevel).where(StockLevel.product_id == product.id))
                session.execute(delete(Product).where(Product.id == product.id))
        engine.dispose()


@pytest.mark.postgres
def test_fifty_concurrent_holds_never_oversell(
    concurrency_stock: tuple[uuid.UUID, uuid.UUID],
) -> None:
    assert API_BASE_URL is not None
    assert TEST_DATABASE_URL is not None
    _wait_for_api()
    product_id, warehouse_id = concurrency_stock
    barrier = threading.Barrier(CONCURRENCY)

    def request_hold(index: int) -> httpx.Response:
        barrier.wait(timeout=20)
        return httpx.post(
            f"{API_BASE_URL}/holds",
            headers={
                "X-Client-Id": f"postgres-concurrency-test-{index}",
                "Idempotency-Key": f"concurrency-{index}",
            },
            json={
                "product_id": str(product_id),
                "warehouse_id": str(warehouse_id),
                "quantity": 1,
            },
            timeout=30,
        )

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        responses = list(executor.map(request_hold, range(CONCURRENCY)))

    response_counts = Counter(response.status_code for response in responses)
    assert response_counts == Counter({201: 3, 409: 47})
    assert all(
        response.json()["error"]["code"] == "insufficient_inventory"
        for response in responses
        if response.status_code == 409
    )

    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            stock_level = session.scalar(
                select(StockLevel).where(
                    StockLevel.product_id == product_id,
                    StockLevel.warehouse_id == warehouse_id,
                )
            )
            holds = list(
                session.scalars(
                    select(Hold).where(
                        Hold.product_id == product_id,
                        Hold.warehouse_id == warehouse_id,
                    )
                )
            )
    finally:
        engine.dispose()

    assert stock_level is not None
    assert stock_level.on_hand == 3
    assert stock_level.held == 3
    assert stock_level.sold == 0
    assert stock_level.held + stock_level.sold <= stock_level.on_hand
    assert len(holds) == 3
    assert all(hold.status is HoldStatus.ACTIVE for hold in holds)
    assert sum(hold.quantity for hold in holds) == 3


def _wait_for_api() -> None:
    assert API_BASE_URL is not None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{API_BASE_URL}/health", timeout=1)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    pytest.fail("API did not become healthy before the concurrency test")
