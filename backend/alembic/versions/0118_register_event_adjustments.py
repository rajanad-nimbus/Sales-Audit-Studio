"""Governed corrections to register-event details."""

import sqlalchemy as sa
from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "register_event_adjustments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(length=36),
            sa.ForeignKey("register_events.id"),
            nullable=False,
        ),
        sa.Column("proposed_values", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="Proposed"),
        sa.Column("proposed_by", sa.String(length=255), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('Proposed', 'Approved', 'Rejected')",
            name="ck_register_event_adjustments_status",
        ),
    )
    op.create_index(
        "ix_register_event_adjustments_event_id",
        "register_event_adjustments",
        ["event_id"],
    )
    op.create_index(
        "ix_register_event_adjustments_status",
        "register_event_adjustments",
        ["status"],
    )
    op.create_index(
        "ix_register_event_adjustments_event_created",
        "register_event_adjustments",
        ["event_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_register_event_adjustments_event_created",
        table_name="register_event_adjustments",
    )
    op.drop_index("ix_register_event_adjustments_status", table_name="register_event_adjustments")
    op.drop_index("ix_register_event_adjustments_event_id", table_name="register_event_adjustments")
    op.drop_table("register_event_adjustments")
