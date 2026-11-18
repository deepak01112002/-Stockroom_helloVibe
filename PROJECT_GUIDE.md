# Stockroom project guide

## Architecture

Stockroom is a FastAPI service with PostgreSQL as the authoritative inventory store. The API and expiry worker share SQLAlchemy 2 service-layer transaction rules. Pydantic 2 defines API input/output models. PostgreSQL, not process-local locks, protects inventory across API instances.

Docker Compose starts PostgreSQL, runs migrations, bootstraps the two warehouses
and a usable catalogue, starts the API, and starts the expiry worker. Startup
uses DummyJSON first and a deterministic fallback product only when the
supplier cannot be reached.

## Inventory model

Exactly two warehouses are seeded: `warehouse-a` (Warehouse A) and `warehouse-b` (Warehouse B). Supplier synchronization ensures these rows exist idempotently, and `scripts/seed_warehouses.py` offers the same seeding operation directly. Supplier stock is split deterministically: Warehouse A receives `ceil(total / 2)` and Warehouse B receives `floor(total / 2)`.

A hold will reserve one product from one warehouse. The migrations create `Product`, `Warehouse`, `StockLevel`, `Hold`, `Order`, `PaymentEvent`, and `SyncRun` tables. Every `StockLevel` has `available = on_hand - held - sold`; the database check constraint enforces `held + sold <= on_hand`.

`Hold` and `Order` use composite foreign keys to `StockLevel(product_id, warehouse_id)`. This makes an allocation explicitly belong to a real product-and-warehouse stock row, rather than allowing mismatched IDs. `Hold` also has a unique `(client_id, idempotency_key)` constraint for the later create-hold API.

## Catalogue API

`GET /products` returns active products in stable title/ID order. It accepts optional exact `category`, optional `in_stock` boolean, and one-based `page` / `page_size` pagination (page size 1-100). The response includes `items`, `page`, `page_size`, `total`, and `total_pages`.

Each item's `available_quantity` is computed in SQL as the aggregate `SUM(on_hand - held - sold)` across its warehouse rows. `in_stock=true` selects totals above zero; `in_stock=false` selects totals equal to zero. This keeps catalogue filtering correct once holds and orders begin changing inventory.

## Transactions and locks

`POST /holds` locks the target `StockLevel` row with `FOR UPDATE`, verifies `on_hand - held - sold`, increases `held`, and inserts the active hold in one transaction. Confirmation and cancellation always lock `Hold` first and `StockLevel` second before changing state/counters. This stable order prevents deadlock cycles.

The concurrency proof runs against the Docker Compose API and PostgreSQL services, not a mock or SQLite substitute. It creates a stock row with three units, sends 50 simultaneous one-unit `POST /holds` requests with unique idempotency keys, and asserts exactly three holds succeed. Final counters must be `on_hand=3`, `held=3`, `sold=0`, satisfying `held + sold <= on_hand`.

Confirmation decreases `held`, increases `sold`, marks the hold confirmed, and creates its one-to-one `Order` in one transaction. Cancellation decreases `held` and marks the hold cancelled in one transaction. Repeating a confirmation returns the original order; repeating a cancellation returns the cancelled hold without another counter change.

The expiry worker selects eligible active holds in expiry-time/ID order with `FOR UPDATE SKIP LOCKED`. For each claimed hold, it locks `Hold` first, then its `StockLevel`, releases `held`, and marks the hold `expired` in one transaction. Multiple workers therefore never process the same committed hold. A crash before commit rolls back the row locks, counter change, and status transition; a later worker can reclaim the hold. `app.worker.expiry` runs this bounded batch loop in Docker Compose, whose worker service restarts unless explicitly stopped.

## Idempotency and payments

`POST /holds` requires `X-Client-Id` and `Idempotency-Key`; `(client_id, idempotency_key)` is unique. The request's product, warehouse, and quantity are SHA-256 hashed canonically. A matching replay returns the original hold and takes stock once; reuse with a different payload returns `409`.

Holds expire 15 minutes after creation. Confirmation rejects an expired active hold; the expiry worker safely releases it. A webhook that discovers an active expired hold performs the same release in its transaction so a late payment cannot confirm it.

`POST /webhooks/payments` verifies `X-Webhook-Signature` as `sha256=<HMAC-SHA256(raw request body)>` with `PAYMENT_WEBHOOK_SECRET`. The body contains `provider_event_id`, `hold_id`, and `event` set to `payment.succeeded` or `payment.failed`. The webhook transaction inserts the unique `PaymentEvent`, locks the `Hold`, locks its `StockLevel` when a transition is needed, applies the payment outcome, and stores a processing result. Any failure rolls back all of it, so delivery can retry safely. A committed duplicate event is acknowledged from its stored result without another inventory transition. Reusing a provider event ID with different signed bytes returns `409`. `scripts/send_test_webhook.py` produces a correctly signed local test event.

A successful payment confirms an active unexpired hold; a failed payment cancels it. If an active hold is already expired when its event is processed, the same transaction expires/releases it and records `hold_expired`; if the worker already expired it, the event is recorded as `already_expired`. Neither late-success path creates an order or re-reserves inventory.

## Supplier sync and rate limits

DummyJSON sync uses HTTPX timeouts and bounded exponential retries for transport errors and 5xx responses. It follows the supplier's `limit`/`skip` pagination, upserts catalogue fields, and reconciles each product into exactly two `StockLevel` rows. Each product upsert/reconciliation runs in a nested transaction: an invalid product or stock reconciliation error is recorded in `SyncRun` and does not stop later products.

For an existing stock row, synchronization locks both of that product's stock rows in ascending warehouse-ID order. It changes `on_hand` only when the incoming target is at least `held + sold`; otherwise the product is left unchanged and the sync records an error. This prevents a supplier stock decrease from breaking the inventory invariant.

`POST /sync/products` and `python scripts/sync_products.py` use the same service. Products are keyed by supplier ID; unchanged source data creates an audit run but does not update catalogue rows.

Hold creation is limited per `X-Client-Id` using PostgreSQL minute buckets, which makes the limit shared across API instances. A conditional PostgreSQL upsert increments a bucket only while its count is below the configured policy (30 requests/minute by default); rejected requests return `429` with `Retry-After`.

## Conventions

Use typed SQLAlchemy 2 declarative models, Pydantic 2 schemas, Alembic migrations with downgrades, and short transactions. Tests that validate concurrency must use real PostgreSQL. HTTP middleware accepts or generates `X-Request-ID`, returns it to the caller, and binds it to structured application logs. All failures use `{ "error": { "code", "message", "request_id" } }`; validation failures additionally include `details`. Do not log secrets or raw webhook signatures.
