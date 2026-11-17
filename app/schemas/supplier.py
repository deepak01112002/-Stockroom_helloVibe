from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SupplierProductsPage(BaseModel):
    """The paginated envelope returned by DummyJSON's products endpoint."""

    model_config = ConfigDict(extra="ignore")

    products: list[dict[str, Any]]
    total: int = Field(ge=0)
    skip: int = Field(ge=0)
    limit: int = Field(ge=0)


class SupplierProduct(BaseModel):
    """The DummyJSON product fields Stockroom persists during catalogue sync."""

    model_config = ConfigDict(extra="ignore")

    id: int
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    category: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(ge=0)
    stock: int = Field(ge=0)


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    products_seen: int
    products_created: int
    products_updated: int
    errors_count: int
    error_summary: str | None
