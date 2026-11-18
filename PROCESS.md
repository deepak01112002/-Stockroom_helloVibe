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
4. “Review PROCESS.md, PROJECT_GUIDE.md, README.md, and the current codebase.” This authorized the final gap audit, shared rate limiting, request-ID logging, CI, and complete PostgreSQL verification.
5. “As per Document and plan audit the entire current repository.” This authorized the assignment-PDF audit, automatic startup seeding, assignment webhook contract, normalized errors, and Azure design expansion.

## Time accounting

Exact elapsed time was not recorded per phase while this repository was built,
so inventing retrospective hours would be misleading. The assignment's 6-8
hour target is therefore **not claimed as verified**. The phase entries below
record the actual implementation and validation work performed; a future
submission should add stopwatch-based elapsed times at the end of each phase.

## AI usage and intentional scope limits

- AI was used for architecture review, typed Python implementation, test
  design, documentation, and command orchestration. All reported command
  results were executed and observed in this workspace.
- No Git commits were created automatically because the user explicitly
  prohibited automatic commits, even though the assignment asks for commit
  history.
- Azure resources were intentionally not provisioned; `DESIGN.md` is a
  production design, while the deliverable remains Docker Compose as requested.

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

### Phase 1 - database models and migrations - 2026-11-18

- Added typed SQLAlchemy 2 models for Product, Warehouse, StockLevel, Hold, Order, and SyncRun.
- Added the initial reversible Alembic migration, `20261118_0001_create_inventory_schema`.
- Enforced the stock invariant in PostgreSQL with `held + sold <= on_hand`, non-negative counters, unique product/warehouse stock rows, positive hold/order quantities, and composite hold/order allocation foreign keys.
- Added model metadata tests for model registration and the inventory constraint.
- The first real migration attempt failed because the migration manually created the PostgreSQL enum and SQLAlchemy then attempted to create it again for the `holds` table. PostgreSQL rolled the transaction back. Removed the duplicate manual enum creation and reran the migration successfully.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran `pytest`: 4 passed; one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Rebuilt the Python 3.12 API container and verified Alembic upgrade, downgrade to base, and re-upgrade on PostgreSQL 16. The final revision is `20261118_0001 (head)`.

### Phase 2 - DummyJSON catalogue synchronization - 2026-11-18

- Added a typed HTTPX DummyJSON client that uses `limit`/`skip` pagination, configurable timeout/page-size/retry settings, and exponential backoff for transport failures and 5xx responses.
- Added `SupplierSyncService`, which records `SyncRun` results, upserts products by supplier ID, converts supplier prices to integer cents, and skips unchanged product writes.
- Used a nested transaction for each product so invalid source data or an individual persistence failure is recorded without stopping remaining products.
- Added `POST /sync/products` and `scripts/sync_products.py`; both call the same service. Supplier stock is intentionally not assigned yet because two-warehouse distribution is the next phase.
- Added tests for pagination, retrying a transient server failure, product upsert idempotency, and continuing after an invalid product.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran `pytest`: 8 passed; one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Rebuilt the Python 3.12 API container. Real `POST /sync/products` created 194 products with zero errors; an immediate second run created and updated zero products. The CLI was also run successfully and created/updated zero products.

### Phase 3 - two-warehouse stock distribution - 2026-11-18

- Added idempotent seeding for exactly `warehouse-a` (Warehouse A) and `warehouse-b` (Warehouse B), including `scripts/seed_warehouses.py`.
- Extended supplier synchronization to create or reconcile two stock rows per product. Warehouse A receives `(stock + 1) // 2`; Warehouse B receives `stock // 2`.
- Existing stock rows are locked before reconciliation. If an incoming target is below that row's `held + sold`, the product transaction rolls back, an error is recorded in `SyncRun`, and later products continue.
- Added tests for even and odd distribution, exact seeded warehouses, repeat-sync idempotency, and continuing after a stock-reconciliation error.
- The first test run had one assertion-conversion error when building a dictionary from a SQLAlchemy result. The implementation was unaffected; materializing rows with `.all()` fixed the test.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran `pytest`: 10 passed; one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Rebuilt the Python 3.12 API container. Real synchronization updated 194 existing products to add stock levels, and a second run made zero updates. PostgreSQL confirmed exactly two warehouses, 194 stock rows per warehouse, and zero products whose two stock totals differed by more than one.

### Phase 4 - product catalogue API - 2026-11-18

- Added `GET /products` with exact category filtering, aggregate in-stock/out-of-stock filtering, and one-based pagination (page size 1-100).
- Implemented availability in SQL as the sum of `on_hand - held - sold` across warehouse rows, rather than trusting an application-side cache.
- Returns active products in stable title/ID order and includes `available_quantity`, page metadata, and total counts.
- Added API tests covering category filtering, in-stock and out-of-stock filtering, aggregation across two warehouses, pagination, and invalid pagination parameters.
- The first test run found an assertion using an expired SQLAlchemy instance after its setup session closed. Capturing the UUID before closing the setup session fixed the test; endpoint behavior was unaffected.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran `pytest`: 12 passed; one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Rebuilt the Python 3.12 API container and verified `GET /products?category=beauty&in_stock=true&page=1&page_size=3` returned a paginated response with 3 items, total 5, and total pages 2.

### Phase 5 - transactional holds and orders - 2026-11-18

- Added create, confirm, and cancel hold endpoints plus typed request/response schemas.
- Hold creation locks one stock row and atomically increments `held` while inserting the hold. Idempotency uses the client/key unique constraint plus a canonical request hash; matching replays return the first hold and conflicting payloads return 409.
- Confirmation and cancellation lock Hold before StockLevel. Confirmation moves quantity from held to sold and inserts the unique order; cancellation returns held stock. Both actions are safe to retry.
- Added API tests for idempotent create, conflicting key reuse, confirm replay, cancel replay, and insufficient inventory.
- The first real PostgreSQL hold request failed because SQLAlchemy bound the Python enum name `ACTIVE`, while the migration's PostgreSQL enum contains the value `active`. The transaction rolled back with no stock change. Configured the ORM enum to persist enum values, then reran verification successfully.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran `pytest`: 13 passed; one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Rebuilt the Python 3.12 API container. Real create/replay returned the same hold; real confirm/replay returned the same order and changed the stock row to held 0, sold 2. A separate real hold/cancel/replay returned the same cancelled hold and left held 0, sold 2.

### Phase 6 - PostgreSQL concurrency proof - 2026-11-18

- Added a `postgres`-marked integration test and a profile-gated Docker Compose test runner. The test targets the running FastAPI API over HTTP and inspects PostgreSQL directly after the requests finish.
- The test creates isolated stock with three available units, releases 50 concurrent one-unit requests using unique idempotency keys, and asserts three `201` responses and 47 `409 insufficient_inventory` responses.
- It verifies the final row is `on_hand=3`, `held=3`, `sold=0`, verifies three active holds totaling three units, and removes its dedicated fixture product afterward.
- The normal local suite skips this test unless `API_BASE_URL` and `TEST_DATABASE_URL` are supplied, preventing accidental use of a non-PostgreSQL test database.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran local `pytest`: 13 passed, 1 skipped, with one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Ran `docker compose --profile test run --rm test` in Python 3.12: 1 PostgreSQL concurrency test passed, 13 non-marked tests deselected, with the same upstream deprecation warning.

### Phase 7 - hold expiry and payment webhooks - 2026-11-18

- Added `PaymentEvent` and the reversible `20261118_0002_add_payment_events` Alembic migration. Provider event IDs are unique; the event stores a payload hash, outcome, processing result, and completion timestamp.
- Added the Compose expiry worker. Each bounded scan claims due active holds with `FOR UPDATE SKIP LOCKED`, then locks/release their stock in the approved Hold-to-StockLevel order within one transaction. A worker crash before commit leaves no committed change and another worker can claim the hold.
- Configured the Compose worker with `restart: unless-stopped`, so an unexpected worker-process crash is restarted and can reclaim rolled-back expiry work.
- Added `POST /webhooks/payments`. It verifies an HMAC-SHA256 signature over the raw body, records the unique event, processes the hold state in the same transaction, and safely acknowledges matching duplicate events. HMAC secrets are configured only through `PAYMENT_WEBHOOK_SECRET` and are not logged.
- Removed the temporary Compose development-secret value before handoff. Compose now requires `PAYMENT_WEBHOOK_SECRET` from `.env` or the environment; no webhook secret is stored in tracked configuration.
- Payment success confirms active holds, payment failure cancels them, and late success records an expiry result without creating an order. A provider event ID reused with different payload bytes is rejected with `409`.
- Added local tests for expiry release/idempotency and signed webhook success/replay/failure/late-event behavior. Added a PostgreSQL lifecycle integration test which starts two concurrent worker batches against ten expired holds and verifies exactly ten releases, then drives webhook flows through the Compose API.
- The first PostgreSQL lifecycle assertion counted orders from pre-existing development data. The implementation was correct; scoping the assertion to the fixture product fixed the test isolation issue.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran local `pytest`: 15 passed, 2 skipped, with one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Ran `docker compose --profile test run --rm test` after a no-cache test-image rebuild: 2 PostgreSQL tests passed, 15 non-marked tests deselected, with the same upstream deprecation warning.

### Phase 8 - shared rate limits, observability, and delivery checks - 2026-11-18

- Added a PostgreSQL `rate_limit_buckets` table and reversible migration. Hold creation now uses a conditional PostgreSQL upsert keyed by client/minute, giving all API instances the same 30-request default limit without process-local state.
- Added request-ID middleware. It preserves a caller-provided `X-Request-ID` or generates one, returns it in the response, and binds it to structured application logs.
- Added `DESIGN.md` and GitHub Actions CI for Ruff, mypy, pytest, Compose validation, and the PostgreSQL Compose suite.
- Corrected the guide’s stale pre-worker wording and documented the operational rate-limit/request-ID behavior.
- Ran `ruff check .`: passed.
- Ran `mypy app`: passed with no issues.
- Ran local `pytest`: 16 passed, 3 skipped, with one Starlette/HTTPX deprecation warning from `fastapi.testclient`.
- Ran `docker compose --profile test run --rm test` after verifying collection of all integration tests: 3 PostgreSQL tests passed, 16 non-marked tests deselected, with the same upstream deprecation warning. The suite included a live DummyJSON synchronization and all required runtime flows.

### Phase 9 - assignment-PDF completion audit - 2026-11-18

- Compared the repository against the supplied assignment PDF. The audit found automatic Compose data bootstrap, assignment event names, signed-webhook script, a uniform validation/error envelope, and the Azure/festival-sale design detail still incomplete.
- Updated startup so the API runs migrations then synchronizes DummyJSON before serving; if the supplier cannot be reached, it inserts a deterministic fallback catalogue item. This was chosen to make a clean machine usable without hiding supplier outages.
- Changed the webhook request contract to `event: payment.succeeded|payment.failed` and added the runnable signing script. The internal persisted outcome remains a compact `succeeded|failed` enum.
- Added common error handling for `HTTPException` and FastAPI validation errors. A validation test caught that old test expectations still referenced FastAPI's `detail` shape; those tests were updated to the documented envelope.
- Expanded `DESIGN.md` with Azure Container Apps, Azure Database for PostgreSQL, Service Bus before asynchronous webhook business processing, Key Vault, and a 500 holds/second contention analysis.
- Ran `ruff check .`: passed.
- Ran `mypy app scripts`: passed with no issues in 34 source files.
- Ran local `pytest`: 18 passed, 3 skipped, with one upstream Starlette/TestClient deprecation warning.
- Ran Docker Compose configuration validation: passed.
- Ran Docker/PostgreSQL integration and E2E suite: 3 passed, 18 non-marked tests deselected, with the same upstream warning. It covered sync, listing, holds/idempotency/confirm/cancel, concurrency, expiry, assignment webhook events/replay, and rate limiting.
- Ran a disposable PostgreSQL database through `alembic upgrade head`, `alembic downgrade base`, and `alembic upgrade head`: all revisions completed successfully; the disposable database was dropped afterward.
- Ran the exact documented `docker compose up --build` startup path. It logged a completed 194-product bootstrap; PostgreSQL reported two warehouses and 388 stock levels, and `/health` returned database OK.
