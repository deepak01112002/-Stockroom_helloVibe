from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class ProductListItem(BaseModel):
    id: uuid.UUID
    supplier_id: str
    title: str
    description: str | None
    category: str
    price_cents: int
    available_quantity: int


class ProductPage(BaseModel):
    items: list[ProductListItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
