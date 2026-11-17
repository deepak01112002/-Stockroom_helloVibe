# Stockroom

Stockroom is a warehouse inventory reservation service built for the HelloVibe Backend Engineer take-home.

## Current milestone

The service now has the Python 3.12/FastAPI foundation, inventory persistence schema, and a DummyJSON catalogue synchronization endpoint. Warehouse stock distribution and shopper inventory workflows remain deferred.

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

`POST /sync/products` fetches every DummyJSON product page, retries transient supplier failures, and upserts catalogue fields using the supplier product ID. Re-running unchanged supplier data creates a new audit run but does not update products. This phase deliberately does not create or adjust stock levels; that happens with the two-warehouse distribution phase.

You can also invoke the same service from the running API container:

```sh
docker compose exec api python scripts/sync_products.py
```

## Planned checks

```sh
ruff check .
mypy app
pytest
```

## Key inventory rule

For every stock level, available inventory is `on_hand - held - sold`; the database invariant is `held + sold <= on_hand`.
