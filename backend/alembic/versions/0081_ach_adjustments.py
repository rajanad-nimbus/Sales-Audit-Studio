"""Add audited bank and store ACH adjustment records."""

import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ach_adjustments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("record_type", sa.String(10), nullable=False),
        sa.Column("store_id", sa.String(50)),
        sa.Column("bank_id", sa.String(50), nullable=False),
        sa.Column("account_reference", sa.String(100)),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("adjustment_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="Active"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_by", sa.String(255)),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("record_type IN ('Bank', 'Store')", name="ck_ach_adjustment_type"),
        sa.CheckConstraint("status IN ('Active', 'Reversed')", name="ck_ach_adjustment_status"),
        sa.CheckConstraint(
            "(record_type = 'Bank' AND store_id IS NULL) OR (record_type = 'Store' AND store_id IS NOT NULL)",
            name="ck_ach_adjustment_scope",
        ),
    )
    op.create_index("ix_ach_adjustments_record_type", "ach_adjustments", ["record_type"])
    op.create_index("ix_ach_adjustments_store_id", "ach_adjustments", ["store_id"])
    op.create_index("ix_ach_adjustments_bank_id", "ach_adjustments", ["bank_id"])
    op.create_index("ix_ach_adjustments_business_date", "ach_adjustments", ["business_date"])
    op.create_index("ix_ach_adjustments_status", "ach_adjustments", ["status"])
    op.create_index("ix_ach_adjustments_bank_day", "ach_adjustments", ["bank_id", "business_date"])
    op.create_index("ix_ach_adjustments_store_day", "ach_adjustments", ["store_id", "business_date"])


def downgrade():
    op.drop_table("ach_adjustments")
