from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HoldStatus(enum.StrEnum):
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentOutcome(enum.StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PaymentEventType(enum.StrEnum):
    SUCCEEDED = "payment.succeeded"
    FAILED = "payment.failed"


HOLD_STATUS_ENUM = Enum(
    HoldStatus,
    name="hold_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
)
PAYMENT_OUTCOME_ENUM = Enum(
    PaymentOutcome,
    name="payment_outcome",
    values_callable=lambda outcome_enum: [outcome.value for outcome in outcome_enum],
)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    price_cents: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)


class StockLevel(Base):
    __tablename__ = "stock_levels"
    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", name="product_warehouse"),
        CheckConstraint("on_hand >= 0 AND held >= 0 AND sold >= 0", name="non_negative"),
        CheckConstraint("held + sold <= on_hand", name="inventory_invariant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT")
    )
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    held: Mapped[int] = mapped_column(Integer, default=0)
    sold: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Hold(Base):
    __tablename__ = "holds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "warehouse_id"],
            ["stock_levels.product_id", "stock_levels.warehouse_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("client_id", "idempotency_key", name="client_idempotency"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column()
    warehouse_id: Mapped[uuid.UUID] = mapped_column()
    client_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[HoldStatus] = mapped_column(HOLD_STATUS_ENUM, default=HoldStatus.ACTIVE)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "warehouse_id"],
            ["stock_levels.product_id", "stock_levels.warehouse_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("quantity > 0", name="positive_quantity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hold_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("holds.id", ondelete="RESTRICT"), unique=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column()
    warehouse_id: Mapped[uuid.UUID] = mapped_column()
    quantity: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider_event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hold_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("holds.id", ondelete="RESTRICT"))
    outcome: Mapped[PaymentOutcome] = mapped_column(PAYMENT_OUTCOME_ENUM)
    payload_hash: Mapped[str] = mapped_column(String(64))
    processing_result: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint("client_id", "window_start", name="client_window"),
        CheckConstraint("request_count >= 0", name="non_negative_request_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(128))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_count: Mapped[int] = mapped_column(Integer, default=0)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("products_seen >= 0", name="non_negative_products_seen"),
        CheckConstraint("products_created >= 0", name="non_negative_products_created"),
        CheckConstraint("products_updated >= 0", name="non_negative_products_updated"),
        CheckConstraint("errors_count >= 0", name="non_negative_errors_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32))
    products_seen: Mapped[int] = mapped_column(Integer, default=0)
    products_created: Mapped[int] = mapped_column(Integer, default=0)
    products_updated: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
