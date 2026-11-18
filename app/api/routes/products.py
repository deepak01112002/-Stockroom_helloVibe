from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.products import ProductListItem, ProductPage
from app.services.products import list_products

router = APIRouter(prefix="/products", tags=["products"])
DatabaseSession = Annotated[Session, Depends(get_db_session)]


@router.get("", response_model=ProductPage)
def get_products(
    session: DatabaseSession,
    category: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    in_stock: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProductPage:
    """Return active products with optional category/availability filters and pagination."""

    result = list_products(
        session,
        category=category,
        in_stock=in_stock,
        page=page,
        page_size=page_size,
    )
    return ProductPage(
        items=[
            ProductListItem(
                id=product.id,
                supplier_id=product.supplier_id,
                title=product.title,
                description=product.description,
                category=product.category,
                price_cents=product.price_cents,
                available_quantity=available_quantity,
            )
            for product, available_quantity in result.items
        ],
        page=page,
        page_size=page_size,
        total=result.total,
        total_pages=(result.total + page_size - 1) // page_size,
    )
