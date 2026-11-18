from __future__ import annotations

import hashlib
import hmac
import json
import sys

import pytest
from app.db.base import Base
from app.db.models import Product, StockLevel, Warehouse
from app.services.supplier_sync import SupplierSyncService
from scripts import send_test_webhook
from scripts.bootstrap_data import FallbackProductSource
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def test_fallback_bootstrap_source_creates_usable_catalogue() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = SupplierSyncService(session, FallbackProductSource()).run()
        session.commit()
        result_status = result.status
        product = session.scalar(select(Product).where(Product.supplier_id == "999001"))
        levels = list(session.scalars(select(StockLevel)))
        warehouses = list(session.scalars(select(Warehouse)))

    assert result_status == "completed"
    assert product is not None
    assert len(warehouses) == 2
    assert sum(level.on_hand for level in levels) == 20


def test_send_test_webhook_uses_assignment_event_and_correct_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

        def raise_for_status(self) -> None:
            return None

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "script-test-secret")
    monkeypatch.setattr(send_test_webhook.httpx, "post", fake_post)
    monkeypatch.setattr(
        sys,
        "argv",
        ["send_test_webhook.py", "hold-123", "--event", "payment.succeeded"],
    )

    send_test_webhook.main()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    payload = kwargs["content"]
    assert isinstance(payload, bytes)
    assert json.loads(payload)["event"] == "payment.succeeded"
    signature = kwargs["headers"]["X-Webhook-Signature"]
    expected = "sha256=" + hmac.new(
        b"script-test-secret", payload, hashlib.sha256
    ).hexdigest()
    assert signature == expected
