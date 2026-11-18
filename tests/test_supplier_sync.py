from __future__ import annotations

from collections.abc import Iterator

from app.db.base import Base
from app.db.models import Product, StockLevel, Warehouse
from app.services.supplier_sync import SupplierSyncService
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


class StaticSupplier:
    def __init__(self, products: list[dict[str, object]]) -> None:
        self._products = products

    def iter_products(self) -> Iterator[dict[str, object]]:
        yield from self._products


def product_payload(product_id: int = 1, title: str = "Desk", stock: int = 10) -> dict[str, object]:
    return {
        "id": product_id,
        "title": title,
        "description": "A desk",
        "category": "furniture",
        "price": "12.34",
        "stock": stock,
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
        warehouses = list(session.scalars(select(Warehouse).order_by(Warehouse.code)))
        assert [(warehouse.code, warehouse.name) for warehouse in warehouses] == [
            ("warehouse-a", "Warehouse A"),
            ("warehouse-b", "Warehouse B"),
        ]
        stock_by_warehouse = {
            warehouse.code: session.scalar(
                select(StockLevel.on_hand).where(
                    StockLevel.product_id
                    == session.scalar(select(Product.id).where(Product.supplier_id == "1")),
                    StockLevel.warehouse_id == warehouse.id,
                )
            )
            for warehouse in warehouses
        }
        assert stock_by_warehouse == {"warehouse-a": 5, "warehouse-b": 5}

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


def test_sync_splits_odd_stock_with_extra_unit_in_warehouse_a() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        SupplierSyncService(session, StaticSupplier([product_payload(stock=5)])).run()
        session.commit()

        stock_by_code = dict(
            session.execute(
                select(Warehouse.code, StockLevel.on_hand)
                .join(StockLevel, StockLevel.warehouse_id == Warehouse.id)
                .order_by(Warehouse.code)
            ).all()
        )
        assert stock_by_code == {"warehouse-a": 3, "warehouse-b": 2}


def test_sync_continues_when_stock_reconciliation_would_break_allocations() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        SupplierSyncService(session, StaticSupplier([product_payload(stock=10)])).run()
        session.commit()
        warehouse_a = session.scalar(select(Warehouse).where(Warehouse.code == "warehouse-a"))
        protected_level = session.scalar(
            select(StockLevel).where(StockLevel.warehouse_id == warehouse_a.id)
        )
        protected_level.held = 5
        session.commit()

        sync_run = SupplierSyncService(
            session,
            StaticSupplier([product_payload(stock=7), product_payload(2, "Chair", stock=5)]),
        ).run()
        session.commit()

        assert sync_run.status == "completed_with_errors"
        assert sync_run.errors_count == 1
        assert session.scalar(select(Product).where(Product.supplier_id == "2")) is not None
