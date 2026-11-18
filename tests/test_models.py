from __future__ import annotations

from app.db.base import Base
from app.db.models import (
    Hold,
    Order,
    PaymentEvent,
    Product,
    RateLimitBucket,
    StockLevel,
    SyncRun,
    Warehouse,
)


def test_inventory_models_are_registered_with_shared_metadata() -> None:
    assert Base.metadata.tables.keys() >= {
        "products",
        "warehouses",
        "stock_levels",
        "holds",
        "orders",
        "payment_events",
        "rate_limit_buckets",
        "sync_runs",
    }
    assert Product.__tablename__ == "products"
    assert Warehouse.__tablename__ == "warehouses"
    assert StockLevel.__tablename__ == "stock_levels"
    assert Hold.__tablename__ == "holds"
    assert Order.__tablename__ == "orders"
    assert PaymentEvent.__tablename__ == "payment_events"
    assert RateLimitBucket.__tablename__ == "rate_limit_buckets"
    assert SyncRun.__tablename__ == "sync_runs"


def test_stock_level_declares_inventory_invariant() -> None:
    constraints = {
        constraint.sqltext.text
        for constraint in StockLevel.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "held + sold <= on_hand" in constraints
