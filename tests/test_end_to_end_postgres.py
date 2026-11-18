from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.db.models import Hold, HoldStatus, Product, StockLevel
from app.worker.expiry import run_once
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

API_BASE_URL = os.getenv("API_BASE_URL")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET")


@pytest.mark.postgres
def test_complete_stockroom_api_flow() -> None:
    if API_BASE_URL is None or TEST_DATABASE_URL is None or WEBHOOK_SECRET is None:
        pytest.skip("requires Docker Compose API, PostgreSQL, and webhook secret")
    _wait_for_api()

    sync = httpx.post(f"{API_BASE_URL}/sync/products", timeout=60)
    assert sync.status_code == 200, sync.text
    assert sync.json()["products_seen"] > 0

    products = httpx.get(
        f"{API_BASE_URL}/products",
        params={"in_stock": "true", "page": 1, "page_size": 5},
        headers={"X-Request-ID": "e2e-request-id"},
        timeout=15,
    )
    assert products.status_code == 200
    assert products.headers["X-Request-ID"] == "e2e-request-id"
    assert products.json()["total"] > 0

    factory = sessionmaker(
        bind=create_engine(TEST_DATABASE_URL), class_=Session, expire_on_commit=False
    )
    with factory() as session:
        stock = session.scalar(
            select(StockLevel)
            .join(Product, Product.id == StockLevel.product_id)
            .where(StockLevel.on_hand - StockLevel.held - StockLevel.sold >= 5)
            .order_by(StockLevel.on_hand.desc())
        )
        assert stock is not None
        product_id, warehouse_id = stock.product_id, stock.warehouse_id

    client_id = f"e2e-client-{uuid.uuid4()}"
    payload = {"product_id": str(product_id), "warehouse_id": str(warehouse_id), "quantity": 1}
    created = _create_hold(client_id, "create", payload)
    replay = _create_hold(client_id, "create", payload)
    assert created.status_code == replay.status_code == 201
    assert created.json()["id"] == replay.json()["id"]

    confirmed = httpx.post(f"{API_BASE_URL}/holds/{created.json()['id']}/confirm", timeout=15)
    confirmed_replay = httpx.post(
        f"{API_BASE_URL}/holds/{created.json()['id']}/confirm", timeout=15
    )
    assert confirmed.status_code == confirmed_replay.status_code == 200
    assert confirmed.json()["id"] == confirmed_replay.json()["id"]

    cancellable = _create_hold(client_id, "cancel", payload)
    cancelled = httpx.delete(f"{API_BASE_URL}/holds/{cancellable.json()['id']}", timeout=15)
    cancelled_replay = httpx.delete(f"{API_BASE_URL}/holds/{cancellable.json()['id']}", timeout=15)
    assert cancelled.json()["status"] == cancelled_replay.json()["status"] == "cancelled"

    payable = _create_hold(client_id, "payment", payload)
    payment = _post_payment(f"e2e-payment-{uuid.uuid4()}", payable.json()["id"], "succeeded")
    payment_replay = _post_payment(
        payment.json()["event"]["provider_event_id"], payable.json()["id"], "succeeded"
    )
    assert payment.json()["result"] == "confirmed"
    assert payment_replay.json()["duplicate"] is True

    expiring = _create_hold(client_id, "expiry", payload)
    with factory() as session, session.begin():
        hold = session.get(Hold, uuid.UUID(expiring.json()["id"]))
        assert hold is not None
        hold.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    run_once(factory, batch_size=100)
    with factory() as session:
        assert session.get(Hold, uuid.UUID(expiring.json()["id"])).status is HoldStatus.EXPIRED

    rate_client = f"e2e-rate-{uuid.uuid4()}"
    rate_responses = [
        _create_hold(rate_client, f"rate-{index}", {**payload, "quantity": 1_000_000})
        for index in range(31)
    ]
    assert all(response.status_code == 409 for response in rate_responses[:30])
    assert rate_responses[30].status_code == 429
    assert rate_responses[30].json()["error"]["code"] == "rate_limit_exceeded"


def _create_hold(client_id: str, key: str, payload: dict[str, object]) -> httpx.Response:
    assert API_BASE_URL is not None
    return httpx.post(
        f"{API_BASE_URL}/holds",
        json=payload,
        headers={"X-Client-Id": client_id, "Idempotency-Key": key},
        timeout=15,
    )


def _post_payment(event_id: str, hold_id: str, outcome: str) -> httpx.Response:
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
    pytest.fail("API did not become healthy before end-to-end testing")
