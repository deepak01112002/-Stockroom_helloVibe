from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import RateLimitBucket


class RateLimitExceededError(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("hold creation rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class RateLimitService:
    """PostgreSQL-backed fixed-window limiter shared by every API instance."""

    def __init__(self, session: Session, limit: int) -> None:
        self._session = session
        self._limit = limit

    def consume_hold_request(self, client_id: str) -> None:
        now = datetime.now(UTC)
        window_start = now.replace(second=0, microsecond=0)
        statement = (
            insert(RateLimitBucket)
            .values(client_id=client_id, window_start=window_start, request_count=1)
            .on_conflict_do_update(
                constraint="uq_rate_limit_buckets_client_window",
                set_={"request_count": RateLimitBucket.request_count + 1},
                where=RateLimitBucket.request_count < self._limit,
            )
            .returning(RateLimitBucket.request_count)
        )
        with self._session.begin():
            accepted_count = self._session.scalar(statement)
        if accepted_count is None:
            next_window = window_start + timedelta(minutes=1)
            retry_after = max(1, int((next_window - now).total_seconds()))
            raise RateLimitExceededError(retry_after)
