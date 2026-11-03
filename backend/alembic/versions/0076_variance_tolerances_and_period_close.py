"""Variance tolerances, variance explanations, and accounting period close.

Readiness, tolerance outcomes and reopen history are derived when read; only the
rules, the explanations people give, and the period close itself are stored.
"""

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "variance_tolerances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("total_name", sa.String(100)),
        sa.Column("location_trait", sa.String(100)),
        sa.Column("within_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("case_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "within_amount >= 0 AND case_amount >= within_amount",
            name="ck_variance_tolerances_amounts",
        ),
    )
    op.create_index(
        "ix_variance_tolerances_scope", "variance_tolerances", ["total_name", "location_trait"]
    )
    op.create_table(
        "variance_explanations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("store_day_id", sa.String(36), sa.ForeignKey("store_days.id"), nullable=False),
        sa.Column(
            "audit_total_id", sa.String(36), sa.ForeignKey("audit_totals.id"), nullable=False
        ),
        sa.Column("audit_version", sa.Integer(), nullable=False),
        sa.Column("total_name", sa.String(100), nullable=False),
        sa.Column("level", sa.String(24), nullable=False),
        sa.Column("dimension_key", sa.String(100)),
        sa.Column("variance_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.id")),
        sa.Column("correction_id", sa.String(36), sa.ForeignKey("transaction_adjustments.id")),
        sa.Column("explained_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_variance_explanations_store_day_id", "variance_explanations", ["store_day_id"]
    )
    op.create_table(
        "period_closes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("period_end", sa.String(10), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("closed_by", sa.String(255), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reopened_by", sa.String(255)),
        sa.Column("reopened_reason", sa.Text()),
        sa.Column("reopened_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_period_closes_period_end", "period_closes", ["period_end"])
    op.create_index("ix_period_closes_status", "period_closes", ["status"])


def downgrade():
    op.drop_table("period_closes")
    op.drop_table("variance_explanations")
    op.drop_table("variance_tolerances")
