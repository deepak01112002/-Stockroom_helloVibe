# Stockroom project guide

## Architecture

Stockroom is a FastAPI service with PostgreSQL as the authoritative inventory store. The API and expiry worker will share SQLAlchemy 2 service-layer transaction rules. Pydantic 2 defines API input/output models. PostgreSQL, not process-local locks, protects inventory across API instances.

## Inventory model

Exactly two warehouses are seeded: `warehouse-a` (Warehouse A) and `warehouse-b` (Warehouse B). Supplier stock is split deterministically: Warehouse A receives `ceil(total / 2)` and Warehouse B receives `floor(total / 2)`.

A hold will reserve one product from one warehouse. For every future `StockLevel`, `available = on_hand - held - sold` and a database check constraint will enforce `held + sold <= on_hand`.

## Transactions and locks

Hold creation will lock the target `StockLevel` row before increasing `held` and inserting the hold. Confirmation, cancellation, expiry, and payment processing will always lock `Hold` first and `StockLevel` second. This stable order prevents deadlock cycles.

The expiry worker will select eligible active holds with `FOR UPDATE SKIP LOCKED`. It will release stock and mark a hold expired in one transaction. A crash before commit rolls back; a later worker can reclaim the work.

## Idempotency and payments

`POST /holds` will require `X-Client-Id` and `Idempotency-Key`; `(client_id, idempotency_key)` will be unique. Reuse with a different request payload will be a conflict.

Payment webhooks will verify an HMAC over the raw request body. Event recording, hold/stock state changes, and the processed marker will commit as one transaction. Provider event IDs will be unique, so duplicate deliveries cannot repeat inventory changes. A payment success received after expiry will be recorded for reconciliation and will not create an order or reserve stock again.

## Supplier sync and rate limits

DummyJSON sync will use HTTP timeouts and bounded exponential retries. Product work will run independently; a stock reconciliation error will be recorded in `SyncRun` and will not stop later products. Incoming stock that would violate the inventory invariant will not be applied.

Hold creation will be limited per `X-Client-Id` using PostgreSQL minute buckets, which makes the limit shared across API instances. The initial policy is 30 hold requests per client per minute.

## Conventions

Use typed SQLAlchemy 2 declarative models, Pydantic 2 schemas, Alembic migrations with downgrades, and short transactions. Tests that validate concurrency must use real PostgreSQL. Keep structured logs and request IDs at the HTTP boundary; do not log secrets or raw webhook signatures.
