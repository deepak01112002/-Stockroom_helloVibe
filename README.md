# Stockroom

Stockroom is a warehouse inventory reservation service built for the HelloVibe Backend Engineer take-home.

## Current milestone

The service now has the Python 3.12/FastAPI foundation, inventory persistence schema, DummyJSON catalogue synchronization, and deterministic two-warehouse stock distribution. Shopper inventory workflows remain deferred.

## Run locally

1. Copy `.env.example` to `.env` if you need to override local settings.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000/docs` and call `GET /health`.

The API container runs `alembic upgrade head` before starting.

## Database schema

The initial Alembic migration creates the inventory persistence model: products, warehouses, stock levels, holds, orders, and supplier synchronization runs. It has been tested with a full local PostgreSQL upgrade, downgrade to base, and re-upgrade.

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

## Planned checks

```sh
ruff check .
mypy app
pytest
```

## Key inventory rule

For every stock level, available inventory is `on_hand - held - sold`; the database invariant is `held + sold <= on_hand`.
