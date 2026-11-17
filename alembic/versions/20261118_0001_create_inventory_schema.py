"""create inventory schema

Revision ID: 20261118_0001
Revises:
Create Date: 2026-11-18 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261118_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    hold_status = sa.Enum("active", "confirmed", "cancelled", "expired", name="hold_status")

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("supplier_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("supplier_id", name="uq_products_supplier_id"),
    )
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_supplier_id", "products", ["supplier_id"])

    op.create_table(
        "warehouses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("code", name="uq_warehouses_code"),
    )

    op.create_table(
        "stock_levels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("on_hand", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("held", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "on_hand >= 0 AND held >= 0 AND sold >= 0", name="ck_stock_levels_non_negative"
        ),
        sa.CheckConstraint("held + sold <= on_hand", name="ck_stock_levels_inventory_invariant"),
        sa.UniqueConstraint("product_id", "warehouse_id", name="uq_stock_levels_product_warehouse"),
    )

    op.create_table(
        "holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", hold_status, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_holds_positive_quantity"),
        sa.ForeignKeyConstraint(
            ["product_id", "warehouse_id"],
            ["stock_levels.product_id", "stock_levels.warehouse_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_holds_client_idempotency"),
    )
    op.create_index("ix_holds_expires_at", "holds", ["expires_at"])

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hold_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("holds.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_orders_positive_quantity"),
        sa.ForeignKeyConstraint(
            ["product_id", "warehouse_id"],
            ["stock_levels.product_id", "stock_levels.warehouse_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("hold_id", name="uq_orders_hold_id"),
    )

    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("products_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.String(length=4000), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("products_seen >= 0", name="ck_sync_runs_non_negative_products_seen"),
        sa.CheckConstraint(
            "products_created >= 0", name="ck_sync_runs_non_negative_products_created"
        ),
        sa.CheckConstraint(
            "products_updated >= 0", name="ck_sync_runs_non_negative_products_updated"
        ),
        sa.CheckConstraint("errors_count >= 0", name="ck_sync_runs_non_negative_errors_count"),
    )


def downgrade() -> None:
    op.drop_table("sync_runs")
    op.drop_table("orders")
    op.drop_index("ix_holds_expires_at", table_name="holds")
    op.drop_table("holds")
    op.drop_table("stock_levels")
    op.drop_table("warehouses")
    op.drop_index("ix_products_supplier_id", table_name="products")
    op.drop_index("ix_products_category", table_name="products")
    op.drop_table("products")
    sa.Enum(name="hold_status").drop(op.get_bind(), checkfirst=True)
