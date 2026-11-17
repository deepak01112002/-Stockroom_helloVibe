# Stockroom project guide

## Architecture

Stockroom is a FastAPI service with PostgreSQL as the authoritative inventory store. The API and expiry worker will share SQLAlchemy 2 service-layer transaction rules. Pydantic 2 defines API input/output models. PostgreSQL, not process-local locks, protects inventory across API instances.

## Inventory model

Exactly two warehouses are seeded: `warehouse-a` (Warehouse A) and `warehouse-b` (Warehouse B). Supplier stock is split deterministically: Warehouse A receives `ceil(total / 2)` and Warehouse B receives `floor(total / 2)`.

A hold will reserve one product from one warehouse. The initial migration now creates `Product`, `Warehouse`, `StockLevel`, `Hold`, `Order`, and `SyncRun` tables. Every `StockLevel` has `available = on_hand - held - sold`; the database check constraint enforces `held + sold <= on_hand`.

`Hold` and `Order` use composite foreign keys to `StockLevel(product_id, warehouse_id)`. This makes an allocation explicitly belong to a real product-and-warehouse stock row, rather than allowing mismatched IDs. `Hold` also has a unique `(client_id, idempotency_key)` constraint for the later create-hold API.

## Transactions and locks

Hold creation will lock the target `StockLevel` row before increasing `held` and inserting the hold. Confirmation, cancellation, expiry, and payment processing will always lock `Hold` first and `StockLevel` second. This stable order prevents deadlock cycles.

The expiry worker will select eligible active holds with `FOR UPDATE SKIP LOCKED`. It will release stock and mark a hold expired in one transaction. A crash before commit rolls back; a later worker can reclaim the work.

## Idempotency and payments

`POST /holds` will require `X-Client-Id` and `Idempotency-Key`; `(client_id, idempotency_key)` will be unique. Reuse with a different request payload will be a conflict.

Payment webhooks will verify an HMAC over the raw request body. Event recording, hold/stock state changes, and the processed marker will commit as one transaction. Provider event IDs will be unique, so duplicate deliveries cannot repeat inventory changes. A payment success received after expiry will be recorded for reconciliation and will not create an order or reserve stock again.

## Supplier sync and rate limits

DummyJSON sync uses HTTPX timeouts and bounded exponential retries for transport errors and 5xx responses. It follows the supplier's `limit`/`skip` pagination and persists only catalogue fields in this phase. Each product upsert runs in a nested transaction: an invalid product is recorded in `SyncRun` and does not stop later products. Stock reconciliation is deferred to the two-warehouse distribution phase; incoming supplier stock is not yet applied to `StockLevel`.

`POST /sync/products` and `python scripts/sync_products.py` use the same service. Products are keyed by supplier ID; unchanged source data creates an audit run but does not update catalogue rows.

Hold creation will be limited per `X-Client-Id` using PostgreSQL minute buckets, which makes the limit shared across API instances. The initial policy is 30 hold requests per client per minute.

## Conventions

Use typed SQLAlchemy 2 declarative models, Pydantic 2 schemas, Alembic migrations with downgrades, and short transactions. Tests that validate concurrency must use real PostgreSQL. Keep structured logs and request IDs at the HTTP boundary; do not log secrets or raw webhook signatures.
