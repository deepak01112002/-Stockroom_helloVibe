from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import Product, StockLevel, SyncRun, Warehouse
from app.schemas.supplier import SupplierProduct, SupplierProductsPage
from app.services.warehouses import ensure_seed_warehouses

PRODUCT_FIELDS = "id,title,description,category,price,stock"


class SupplierSyncError(RuntimeError):
    """Raised when a supplier page cannot be retrieved safely."""


class StockReconciliationError(RuntimeError):
    """Raised when incoming supplier stock would invalidate an existing allocation."""


class ProductPageSource(Protocol):
    def iter_products(self) -> Iterator[dict[str, object]]: ...


@dataclass
class DummyJsonClient:
    base_url: str
    timeout_seconds: float
    max_retries: int
    page_size: int
    client: httpx.Client | None = None

    def iter_products(self) -> Iterator[dict[str, object]]:
        """Yield every supplier product, following DummyJSON's limit/skip pagination."""

        skip = 0
        with self._client_context() as client:
            while True:
                page = self._fetch_page(client, skip)
                if not page.products:
                    if skip < page.total:
                        raise SupplierSyncError(
                            "supplier returned an empty page before the declared total"
                        )
                    return

                yield from page.products
                skip += len(page.products)
                if skip >= page.total:
                    return

    def _fetch_page(self, client: httpx.Client, skip: int) -> SupplierProductsPage:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = client.get(
                    "/products",
                    params={"limit": self.page_size, "skip": skip, "select": PRODUCT_FIELDS},
                )
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "supplier returned a retryable server error",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return SupplierProductsPage.model_validate(response.json())
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt == self.max_retries or (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500
                ):
                    break
                time.sleep(0.25 * (2**attempt))
            except ValidationError as exc:
                raise SupplierSyncError("supplier returned an invalid page envelope") from exc

        raise SupplierSyncError("supplier request failed after configured retries") from last_error

    def _client_context(self) -> httpx.Client:
        if self.client is not None:
            return self.client
        return httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds)


class SupplierSyncService:
    def __init__(self, session: Session, source: ProductPageSource) -> None:
        self._session = session
        self._source = source

    def run(self) -> SyncRun:
        sync_run = SyncRun(status="running")
        self._session.add(sync_run)
        self._session.flush()
        errors: list[str] = []
        warehouses = ensure_seed_warehouses(self._session)

        try:
            for raw_product in self._source.iter_products():
                sync_run.products_seen += 1
                try:
                    product = SupplierProduct.model_validate(raw_product)
                    with self._session.begin_nested():
                        persisted_product, created, catalogue_changed = self._upsert_product(
                            product
                        )
                        stock_changed = self._reconcile_stock_levels(
                            persisted_product, product.stock, warehouses
                        )
                    if created:
                        sync_run.products_created += 1
                    elif catalogue_changed or stock_changed:
                        sync_run.products_updated += 1
                except (StockReconciliationError, ValidationError, SQLAlchemyError) as exc:
                    sync_run.errors_count += 1
                    errors.append(f"product {raw_product.get('id', 'unknown')}: {exc}")

            sync_run.status = "completed_with_errors" if errors else "completed"
        except SupplierSyncError as exc:
            sync_run.status = "failed"
            sync_run.errors_count += 1
            errors.append(str(exc))

        sync_run.error_summary = self._serialize_errors(errors)
        sync_run.finished_at = datetime.now(UTC)
        self._session.flush()
        return sync_run

    def _upsert_product(self, source: SupplierProduct) -> tuple[Product, bool, bool]:
        supplier_id = str(source.id)
        product = self._session.scalar(select(Product).where(Product.supplier_id == supplier_id))
        price_cents = self._price_to_cents(source.price)

        if product is None:
            product = Product(
                supplier_id=supplier_id,
                title=source.title,
                description=source.description,
                category=source.category,
                price_cents=price_cents,
                active=True,
            )
            self._session.add(product)
            self._session.flush()
            return product, True, True

        incoming = (source.title, source.description, source.category, price_cents, True)
        current = (
            product.title,
            product.description,
            product.category,
            product.price_cents,
            product.active,
        )
        if incoming != current:
            (
                product.title,
                product.description,
                product.category,
                product.price_cents,
                product.active,
            ) = incoming
            return product, False, True
        return product, False, False

    def _reconcile_stock_levels(
        self, product: Product, supplier_stock: int, warehouses: dict[str, Warehouse]
    ) -> bool:
        """Set two warehouse totals without ever reducing on-hand below allocated inventory."""

        targets = {
            "warehouse-a": (supplier_stock + 1) // 2,
            "warehouse-b": supplier_stock // 2,
        }
        warehouse_ids = [warehouse.id for warehouse in warehouses.values()]
        existing_levels = list(
            self._session.scalars(
                select(StockLevel)
                .where(
                    StockLevel.product_id == product.id,
                    StockLevel.warehouse_id.in_(warehouse_ids),
                )
                .order_by(StockLevel.warehouse_id)
                .with_for_update()
            )
        )
        levels_by_warehouse_id = {level.warehouse_id: level for level in existing_levels}

        for code, target in targets.items():
            existing_level = levels_by_warehouse_id.get(warehouses[code].id)
            if existing_level is not None and target < existing_level.held + existing_level.sold:
                raise StockReconciliationError(
                    f"incoming stock target for {code} ({target}) is below "
                    f"allocated inventory ({existing_level.held + existing_level.sold})"
                )

        changed = False
        for code, target in targets.items():
            warehouse = warehouses[code]
            stock_level = levels_by_warehouse_id.get(warehouse.id)
            if stock_level is None:
                self._session.add(
                    StockLevel(
                        product_id=product.id,
                        warehouse_id=warehouse.id,
                        on_hand=target,
                        held=0,
                        sold=0,
                    )
                )
                changed = True
            elif stock_level.on_hand != target:
                stock_level.on_hand = target
                changed = True
        return changed

    @staticmethod
    def _price_to_cents(price: Decimal) -> int:
        return int((price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _serialize_errors(errors: list[str]) -> str | None:
        if not errors:
            return None
        return json.dumps(errors, separators=(",", ":"))[:4000]
