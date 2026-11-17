from __future__ import annotations

from collections.abc import Iterator

from app.db.base import Base
from app.db.models import Product
from app.services.supplier_sync import SupplierSyncService
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


class StaticSupplier:
    def __init__(self, products: list[dict[str, object]]) -> None:
        self._products = products

    def iter_products(self) -> Iterator[dict[str, object]]:
        yield from self._products


def product_payload(product_id: int = 1, title: str = "Desk") -> dict[str, object]:
    return {
        "id": product_id,
        "title": title,
        "description": "A desk",
        "category": "furniture",
        "price": "12.34",
        "stock": 10,
    }


def test_sync_upserts_and_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        first_run = SupplierSyncService(session, StaticSupplier([product_payload()])).run()
        session.commit()
        assert first_run.status == "completed"
        assert first_run.products_created == 1
        assert first_run.products_updated == 0
        assert session.scalar(select(Product).where(Product.supplier_id == "1")).price_cents == 1234

        second_run = SupplierSyncService(session, StaticSupplier([product_payload()])).run()
        session.commit()
        assert second_run.status == "completed"
        assert second_run.products_created == 0
        assert second_run.products_updated == 0


def test_sync_continues_after_an_invalid_product() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    products = [
        {"id": 1, "title": "", "category": "furniture", "price": "1.00", "stock": 1},
        product_payload(2, "Chair"),
    ]
    with Session(engine) as session:
        sync_run = SupplierSyncService(session, StaticSupplier(products)).run()
        session.commit()

        assert sync_run.status == "completed_with_errors"
        assert sync_run.products_seen == 2
        assert sync_run.errors_count == 1
        assert sync_run.products_created == 1
        assert session.scalar(select(Product).where(Product.supplier_id == "2")) is not None
