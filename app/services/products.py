from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Product, StockLevel


@dataclass(frozen=True)
class ProductListResult:
    items: list[tuple[Product, int]]
    total: int


def list_products(
    session: Session,
    *,
    category: str | None,
    in_stock: bool | None,
    page: int,
    page_size: int,
) -> ProductListResult:
    """List active catalogue products with availability aggregated across warehouses."""

    availability = (
        select(
            StockLevel.product_id.label("product_id"),
            func.sum(StockLevel.on_hand - StockLevel.held - StockLevel.sold).label(
                "available_quantity"
            ),
        )
        .group_by(StockLevel.product_id)
        .subquery()
    )
    available_quantity = func.coalesce(availability.c.available_quantity, 0)
    statement = select(Product, available_quantity).outerjoin(
        availability, availability.c.product_id == Product.id
    )
    statement = statement.where(Product.active.is_(True))

    if category is not None:
        statement = statement.where(Product.category == category)
    if in_stock is True:
        statement = statement.where(available_quantity > 0)
    elif in_stock is False:
        statement = statement.where(available_quantity == 0)

    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = session.execute(
        statement.order_by(Product.title.asc(), Product.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ProductListResult(
        items=[(product, int(quantity)) for product, quantity in rows], total=total or 0
    )
