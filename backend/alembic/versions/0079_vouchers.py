"""Voucher ledger: issue, redeem once, and escheat expired unclaimed vouchers."""

import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vouchers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("voucher_number", sa.String(100), nullable=False, unique=True),
        sa.Column("issued_store_id", sa.String(50), nullable=False),
        sa.Column("issued_on", sa.String(10), nullable=False),
        sa.Column("expires_on", sa.String(10)),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("issued_by", sa.String(255), nullable=False),
        sa.Column("redeemed_store_id", sa.String(50)),
        sa.Column("redeemed_on", sa.String(10)),
        sa.Column("redeemed_transaction_id", sa.String(36)),
        sa.Column("redeemed_by", sa.String(255)),
        sa.Column("escheated_on", sa.String(10)),
        sa.Column("escheated_by", sa.String(255)),
        sa.Column("escheat_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_vouchers_amount_positive"),
        sa.CheckConstraint(
            "status IN ('Issued', 'Redeemed', 'Escheated')", name="ck_vouchers_status"
        ),
    )
    op.create_index("ix_vouchers_issued_store_id", "vouchers", ["issued_store_id"])
    op.create_index("ix_vouchers_expires_on", "vouchers", ["expires_on"])
    op.create_index("ix_vouchers_status", "vouchers", ["status"])
    op.create_index("ix_vouchers_status_expires", "vouchers", ["status", "expires_on"])


def downgrade():
    op.drop_table("vouchers")
