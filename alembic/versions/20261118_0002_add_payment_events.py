"""add payment event replay protection

Revision ID: 20261118_0002
Revises: 20261118_0001
Create Date: 2026-11-18 00:30:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261118_0002"
down_revision = "20261118_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payment_outcome = sa.Enum("succeeded", "failed", name="payment_outcome")
    op.create_table(
        "payment_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column(
            "hold_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("holds.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("outcome", payment_outcome, nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("processing_result", sa.String(length=64), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("provider_event_id", name="uq_payment_events_provider_event_id"),
    )
    op.create_index("ix_payment_events_provider_event_id", "payment_events", ["provider_event_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_events_provider_event_id", table_name="payment_events")
    op.drop_table("payment_events")
    sa.Enum(name="payment_outcome").drop(op.get_bind(), checkfirst=True)
