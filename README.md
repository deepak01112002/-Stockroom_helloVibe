# Stockroom

Stockroom is a warehouse inventory reservation service built for the HelloVibe Backend Engineer take-home.

## Current milestone

The service includes product synchronization/listing, transactional holds/orders, a safe expiry worker, and HMAC-verified payment webhooks.

## Run locally

1. Install Docker Desktop (or Docker Engine plus Compose) and Git. No local Python or PostgreSQL installation is required to run the stack.
2. Copy `.env.example` to `.env`, then set a unique `PAYMENT_WEBHOOK_SECRET`.
3. Run `docker compose up --build`.
4. Open `http://localhost:8000/docs` and call `GET /health`.

The API container runs every migration and bootstraps the two warehouses plus a
usable DummyJSON catalogue before starting. If DummyJSON is unavailable, it
creates one deterministic fallback product so local development remains usable.

## Database schema

The Alembic migrations create the inventory persistence model: products, warehouses, stock levels, holds, orders, payment events, and supplier synchronization runs. Every migration has a downgrade.

Run migrations manually inside the API service with:

```sh
docker compose exec api alembic upgrade head
```

## Supplier synchronization

`POST /sync/products` fetches every DummyJSON product page, retries transient supplier failures, and upserts catalogue fields using the supplier product ID. It also ensures the only two warehouses exist and reconciles the supplier total into two stock rows: Warehouse A receives the ceiling half and Warehouse B the floor half. Re-running unchanged supplier data creates a new audit run but does not update products or stock rows.

You can also invoke the same service from the running API container:

```sh
docker compose exec api python scripts/sync_products.py
```

To seed only the two warehouses without calling DummyJSON:

```sh
docker compose exec api python scripts/seed_warehouses.py
```

## Product catalogue API

`GET /products` returns active products with aggregate availability across both warehouses. It supports optional `category`, `in_stock`, `page`, and `page_size` query parameters.

```sh
curl 'http://localhost:8000/products?category=beauty&in_stock=true&page=1&page_size=20'
```

The response includes the current page, total matching products, total pages, and each product's `available_quantity`.

## Inventory holds

Create a single-warehouse hold with `X-Client-Id` and `Idempotency-Key` headers:

```sh
curl -X POST http://localhost:8000/holds \
  -H 'Content-Type: application/json' \
  -H 'X-Client-Id: demo-client' \
  -H 'Idempotency-Key: unique-request-key' \
  --data '{"product_id":"...","warehouse_id":"...","quantity":1}'
```

The same client/key/payload returns the original hold without reserving stock again. Confirm with `POST /holds/{hold_id}/confirm`; cancel with `DELETE /holds/{hold_id}`. Confirmation moves the quantity from held to sold; cancellation releases held quantity.

## Expiry worker

Docker Compose starts `app.worker.expiry`. It periodically claims due active holds with PostgreSQL `FOR UPDATE SKIP LOCKED`, then releases inventory and marks them expired atomically. Multiple workers can run safely because a claimed row is skipped by the others until the transaction commits or rolls back.

Tune its batch size and polling interval with `EXPIRY_WORKER_BATCH_SIZE` and `EXPIRY_WORKER_INTERVAL_SECONDS`.

## Payment webhooks

Set a unique `PAYMENT_WEBHOOK_SECRET` outside source control. The endpoint expects `POST /webhooks/payments` with this JSON body:

```json
{"provider_event_id":"provider-event-123","hold_id":"...","event":"payment.succeeded"}
```

Sign the exact request bytes with HMAC-SHA256 and send `X-Webhook-Signature: sha256=<hex digest>`. A provider event ID is processed once. A repeated delivery returns the original result without changing inventory again. Success confirms an active hold; failure cancels it; a late payment is recorded but cannot recreate a released reservation.

Use assignment event names `payment.succeeded` and `payment.failed`. To send a
correctly signed local event after creating a hold:

```sh
export PAYMENT_WEBHOOK_SECRET='the value from your .env'
python scripts/send_test_webhook.py HOLD_ID --event payment.succeeded
```

## PostgreSQL concurrency proof

Run the real 50-request/3-unit concurrency test against the Docker API and PostgreSQL services:

```sh
docker compose --profile test run --rm test
```

The test creates isolated fixture data, verifies exactly three successful holds, checks final database counters, and removes its fixture data afterward.

The same command runs the complete PostgreSQL suite: a live DummyJSON sync and
catalogue request, create/replay/confirm/cancel holds, payment replay, expiry,
rate limiting, the two-worker lifecycle flow, and the 50-request/3-unit
concurrency proof.

## Rate limits and request IDs

`POST /holds` uses a shared PostgreSQL fixed-minute bucket per `X-Client-Id`.
The default is 30 requests per minute and excess requests receive `429` with
`Retry-After`. Set `HOLD_RATE_LIMIT_PER_MINUTE` to adjust it.

Every HTTP response includes `X-Request-ID`. Supply that header to preserve a
caller-generated ID through structured application logs and the response.

## Continuous integration

GitHub Actions runs Ruff, mypy, pytest, and the Docker Compose PostgreSQL suite
on pushes to `main` and pull requests.

## Planned checks

```sh
ruff check .
mypy app
pytest
```

Run the complete Docker/PostgreSQL suite with:

```sh
PAYMENT_WEBHOOK_SECRET=local-test-secret docker compose --profile test run --rm test
```

All errors have this shape, including FastAPI request validation errors:

```json
{"error":{"code":"validation_error","message":"request validation failed","request_id":"...","details":[...]}}
```

## Key inventory rule

For every stock level, available inventory is `on_hand - held - sold`; the database invariant is `held + sold <= on_hand`.
