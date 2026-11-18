from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

from app.api.routes.holds import get_db_session
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import Hold, HoldStatus, PaymentEvent, Product, StockLevel, Warehouse
from app.main import app
from app.services.inventory import InventoryService
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

WEBHOOK_SECRET = "test-webhook-secret"


def test_payment_webhook_is_signed_idempotent_and_handles_terminal_hold_states() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        product = Product(
            supplier_id="payment-product",
            title="Payment Product",
            description=None,
            category="test",
            price_cents=100,
        )
        warehouse = Warehouse(code="warehouse-a", name="Warehouse A")
        session.add_all([product, warehouse])
        session.flush()
        stock_level = StockLevel(product_id=product.id, warehouse_id=warehouse.id, on_hand=10)
        session.add(stock_level)
        session.commit()
        service = InventoryService(session, hold_ttl_minutes=15)
        success_hold = service.create_hold(
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity=2,
            client_id="payment-client",
            idempotency_key="success",
        )
        failed_hold = service.create_hold(
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity=1,
            client_id="payment-client",
            idempotency_key="failed",
        )
        late_hold = service.create_hold(
            product_id=product.id,
            warehouse_id=warehouse.id,
            quantity=1,
            client_id="payment-client",
            idempotency_key="late",
        )
        late_hold.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        success_hold_id, failed_hold_id, late_hold_id = (
            success_hold.id,
            failed_hold.id,
            late_hold.id,
        )

    def override_session() -> Generator[Session, None, None]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    def override_settings() -> Settings:
        return Settings(payment_webhook_secret=SecretStr(WEBHOOK_SECRET))

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    try:
        client = TestClient(app)
        success_body = _payload("event-success", success_hold_id, "succeeded")
        success = _post_signed(client, success_body)
        replay = _post_signed(client, success_body)
        failed = _post_signed(client, _payload("event-failed", failed_hold_id, "failed"))
        late = _post_signed(client, _payload("event-late", late_hold_id, "succeeded"))
        invalid_signature = client.post(
            "/webhooks/payments",
            content=_payload("event-invalid", success_hold_id, "succeeded"),
            headers={"X-Webhook-Signature": "sha256=not-a-signature"},
        )
    finally:
        app.dependency_overrides.clear()

    assert success.status_code == 200
    assert success.json()["result"] == "confirmed"
    assert success.json()["duplicate"] is False
    assert success.json()["order_id"] is not None
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["order_id"] == success.json()["order_id"]
    assert failed.json()["result"] == "cancelled"
    assert late.json()["result"] == "hold_expired"
    assert invalid_signature.status_code == 401

    with Session(engine) as session:
        stock_level = session.scalar(select(StockLevel))
        holds = {hold.id: hold for hold in session.scalars(select(Hold))}
        events = list(session.scalars(select(PaymentEvent)))
    assert stock_level is not None
    assert (stock_level.held, stock_level.sold) == (0, 2)
    assert holds[success_hold_id].status is HoldStatus.CONFIRMED
    assert holds[failed_hold_id].status is HoldStatus.CANCELLED
    assert holds[late_hold_id].status is HoldStatus.EXPIRED
    assert len(events) == 3


def _payload(event_id: str, hold_id: object, outcome: str) -> bytes:
    return json.dumps(
        {"provider_event_id": event_id, "hold_id": str(hold_id), "event": f"payment.{outcome}"},
        separators=(",", ":"),
    ).encode()


def _post_signed(client: TestClient, body: bytes) -> Response:
    signature = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/payments",
        content=body,
        headers={"X-Webhook-Signature": signature},
    )
