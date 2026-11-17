# Stockroom

Stockroom is a warehouse inventory reservation service built for the HelloVibe Backend Engineer take-home.

## Current milestone

The initial setup establishes the Python 3.12/FastAPI project, Pydantic Settings configuration, SQLAlchemy 2 session plumbing, Alembic configuration, PostgreSQL Docker Compose service, JSON logging, and a database-backed health endpoint. Business models and inventory workflows are intentionally deferred.

## Run locally

1. Copy `.env.example` to `.env` and set a non-default local webhook secret.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000/docs` and call `GET /health`.

The API container runs `alembic upgrade head` before starting. Schema migrations, warehouse seeding, and business endpoints will be introduced in subsequent milestones.

## Planned checks

```sh
uv run ruff check .
uv run mypy src
uv run pytest
```

## Key inventory rule

For every stock level, available inventory is `on_hand - held - sold`; the database invariant is `held + sold <= on_hand`.
