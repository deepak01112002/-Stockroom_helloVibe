# Stockroom development process

## Requirements review - 2026-11-18

- The assignment requires Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL, Docker Compose, pytest/HTTPX, Ruff, mypy, GitHub Actions, and the Stockroom inventory workflows.
- PostgreSQL is the inventory source of truth; Python in-memory locks are not acceptable for multi-instance correctness.
- The key invariant is `held + sold <= on_hand`.

## Confirmed design decisions

- Seed exactly Warehouse A and Warehouse B.
- Split supplier stock with ceiling half to Warehouse A and floor half to Warehouse B.
- A hold allocates one product in one warehouse.
- Existing-hold transitions lock Hold before StockLevel.
- Webhook event persistence and state transitions are one transaction; unique provider event IDs make retries safe.
- Sync continues after individual stock-reconciliation errors, recording errors in the sync run.
- Late successful payments after expiry are rejected for reconciliation rather than recreating a reservation.
- Shared PostgreSQL minute buckets rate-limit hold creation by `X-Client-Id`.

## Actual AI prompts used

1. “Before writing any code, design the Stockroom application.” This established the architecture and its trade-offs before implementation.
2. “Validate this point also before moving forward.” This fixed the two-warehouse split, lock order, webhook retry semantics, and per-product sync isolation.
3. “PLEASE IMPLEMENT THIS PLAN.” This authorized the first incremental implementation task.

## Implementation and verification log

### Initial project foundation - 2026-11-18

- This initial foundation was superseded before release to match the requested clean `app/` project layout and to defer domain models/migrations until the business-logic milestone.

### Clean application setup - 2026-11-18

- Created the requested `app/` layout for FastAPI, core configuration/logging, database plumbing, API routes, schemas, services, and worker code.
- Implemented Pydantic Settings, SQLAlchemy 2 engine/session handling, JSON logging startup configuration, and `GET /health` with an actual `SELECT 1` database check.
- Kept Alembic configured but intentionally created no business-schema migration or inventory model.
- Updated Docker Compose to start PostgreSQL and the FastAPI container using `app.main:app`.
- Kept secrets out of tracked files. The only database URL default is a clearly documented local-development value; Docker injects its connection URL through the environment.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran `pytest`: 2 passed. The test run emitted one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Ran `docker compose config --quiet`: passed.
- Started the Docker Compose stack successfully using its Python 3.12 API container and PostgreSQL 16 service.
- Verified `GET /health` returned `{"status":"ok","database":"ok"}`.
- Verified PostgreSQL readiness with `pg_isready` and a `SELECT 1` query inside the database container.
