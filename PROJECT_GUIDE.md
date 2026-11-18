# Stockroom project guide

## Architecture

Stockroom is a FastAPI service with PostgreSQL as the authoritative inventory store. The API and expiry worker will share SQLAlchemy 2 service-layer transaction rules. Pydantic 2 defines API input/output models. PostgreSQL, not process-local locks, protects inventory across API instances.

## Inventory model

Exactly two warehouses are seeded: `warehouse-a` (Warehouse A) and `warehouse-b` (Warehouse B). Supplier synchronization ensures these rows exist idempotently, and `scripts/seed_warehouses.py` offers the same seeding operation directly. Supplier stock is split deterministically: Warehouse A receives `ceil(total / 2)` and Warehouse B receives `floor(total / 2)`.

A hold will reserve one product from one warehouse. The initial migration now creates `Product`, `Warehouse`, `StockLevel`, `Hold`, `Order`, and `SyncRun` tables. Every `StockLevel` has `available = on_hand - held - sold`; the database check constraint enforces `held + sold <= on_hand`.

`Hold` and `Order` use composite foreign keys to `StockLevel(product_id, warehouse_id)`. This makes an allocation explicitly belong to a real product-and-warehouse stock row, rather than allowing mismatched IDs. `Hold` also has a unique `(client_id, idempotency_key)` constraint for the later create-hold API.

## Catalogue API

`GET /products` returns active products in stable title/ID order. It accepts optional exact `category`, optional `in_stock` boolean, and one-based `page` / `page_size` pagination (page size 1-100). The response includes `items`, `page`, `page_size`, `total`, and `total_pages`.

Each item's `available_quantity` is computed in SQL as the aggregate `SUM(on_hand - held - sold)` across its warehouse rows. `in_stock=true` selects totals above zero; `in_stock=false` selects totals equal to zero. This keeps catalogue filtering correct once holds and orders begin changing inventory.

## Transactions and locks

`POST /holds` locks the target `StockLevel` row with `FOR UPDATE`, verifies `on_hand - held - sold`, increases `held`, and inserts the active hold in one transaction. Confirmation and cancellation always lock `Hold` first and `StockLevel` second before changing state/counters. This stable order prevents deadlock cycles.

Confirmation decreases `held`, increases `sold`, marks the hold confirmed, and creates its one-to-one `Order` in one transaction. Cancellation decreases `held` and marks the hold cancelled in one transaction. Repeating a confirmation returns the original order; repeating a cancellation returns the cancelled hold without another counter change.

The expiry worker will select eligible active holds with `FOR UPDATE SKIP LOCKED`. It will release stock and mark a hold expired in one transaction. A crash before commit rolls back; a later worker can reclaim the work.

## Idempotency and payments

`POST /holds` requires `X-Client-Id` and `Idempotency-Key`; `(client_id, idempotency_key)` is unique. The request's product, warehouse, and quantity are SHA-256 hashed canonically. A matching replay returns the original hold and takes stock once; reuse with a different payload returns `409`.

Holds expire 15 minutes after creation. Until the expiry worker phase is implemented, confirmation rejects expired holds but does not release them itself; the documented worker will perform that release safely.

Payment webhooks will verify an HMAC over the raw request body. Event recording, hold/stock state changes, and the processed marker will commit as one transaction. Provider event IDs will be unique, so duplicate deliveries cannot repeat inventory changes. A payment success received after expiry will be recorded for reconciliation and will not create an order or reserve stock again.

## Supplier sync and rate limits

DummyJSON sync uses HTTPX timeouts and bounded exponential retries for transport errors and 5xx responses. It follows the supplier's `limit`/`skip` pagination, upserts catalogue fields, and reconciles each product into exactly two `StockLevel` rows. Each product upsert/reconciliation runs in a nested transaction: an invalid product or stock reconciliation error is recorded in `SyncRun` and does not stop later products.

For an existing stock row, synchronization locks both of that product's stock rows in ascending warehouse-ID order. It changes `on_hand` only when the incoming target is at least `held + sold`; otherwise the product is left unchanged and the sync records an error. This prevents a supplier stock decrease from breaking the inventory invariant.

`POST /sync/products` and `python scripts/sync_products.py` use the same service. Products are keyed by supplier ID; unchanged source data creates an audit run but does not update catalogue rows.

Hold creation will be limited per `X-Client-Id` using PostgreSQL minute buckets, which makes the limit shared across API instances. The initial policy is 30 hold requests per client per minute.

## Conventions

Use typed SQLAlchemy 2 declarative models, Pydantic 2 schemas, Alembic migrations with downgrades, and short transactions. Tests that validate concurrency must use real PostgreSQL. Keep structured logs and request IDs at the HTTP boundary; do not log secrets or raw webhook signatures.
