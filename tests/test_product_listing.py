from __future__ import annotations

from collections.abc import Generator

from app.api.routes.products import get_db_session
from app.db.base import Base
from app.db.models import Product, StockLevel, Warehouse
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def test_products_support_category_stock_filters_and_pagination() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        warehouse_a = Warehouse(code="warehouse-a", name="Warehouse A")
        warehouse_b = Warehouse(code="warehouse-b", name="Warehouse B")
        available = Product(
            supplier_id="1",
            title="Available Desk",
            description=None,
            category="furniture",
            price_cents=1000,
        )
        unavailable = Product(
            supplier_id="2",
            title="Reserved Chair",
            description=None,
            category="furniture",
            price_cents=2000,
        )
        other_category = Product(
            supplier_id="3",
            title="Tablet",
            description=None,
            category="electronics",
            price_cents=3000,
        )
        session.add_all([warehouse_a, warehouse_b, available, unavailable, other_category])
        session.flush()
        session.add_all(
            [
                StockLevel(product_id=available.id, warehouse_id=warehouse_a.id, on_hand=5),
                StockLevel(product_id=available.id, warehouse_id=warehouse_b.id, on_hand=4, held=1),
                StockLevel(
                    product_id=unavailable.id, warehouse_id=warehouse_a.id, on_hand=2, held=2
                ),
                StockLevel(product_id=unavailable.id, warehouse_id=warehouse_b.id, on_hand=0),
                StockLevel(product_id=other_category.id, warehouse_id=warehouse_a.id, on_hand=1),
                StockLevel(product_id=other_category.id, warehouse_id=warehouse_b.id, on_hand=1),
            ]
        )
        available_id = available.id
        session.commit()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        client = TestClient(app)
        available_response = client.get(
            "/products?category=furniture&in_stock=true&page=1&page_size=1"
        )
        unavailable_response = client.get("/products?in_stock=false")
    finally:
        app.dependency_overrides.clear()

    assert available_response.status_code == 200
    assert available_response.json() == {
        "items": [
            {
                "id": str(available_id),
                "supplier_id": "1",
                "title": "Available Desk",
                "description": None,
                "category": "furniture",
                "price_cents": 1000,
                "available_quantity": 8,
            }
        ],
        "page": 1,
        "page_size": 1,
        "total": 1,
        "total_pages": 1,
    }
    assert unavailable_response.status_code == 200
    assert unavailable_response.json()["items"][0]["title"] == "Reserved Chair"
    assert unavailable_response.json()["items"][0]["available_quantity"] == 0


def test_products_validate_pagination_parameters() -> None:
    response = TestClient(app).get("/products?page=0&page_size=101")

    assert response.status_code == 422
