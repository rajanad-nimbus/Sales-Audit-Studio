"""Configure voucher escheat windows by tender type."""

import sqlalchemy as sa
from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vouchers",
        sa.Column("tender_type", sa.String(length=100), nullable=False, server_default="Voucher"),
    )
    op.create_index("ix_vouchers_tender_type", "vouchers", ["tender_type"])
    op.create_table(
        "voucher_options",
        sa.Column("tender_type", sa.String(length=100), primary_key=True),
        sa.Column("escheat_days", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "escheat_days BETWEEN 0 AND 36500", name="ck_voucher_options_escheat_days_nonnegative"
        ),
        sa.CheckConstraint(
            "length(trim(tender_type)) > 0", name="ck_voucher_options_tender_required"
        ),
    )
    op.create_index(
        "uq_voucher_options_tender_lower",
        "voucher_options",
        [sa.text("lower(tender_type)")],
        unique=True,
    )


def downgrade():
    op.drop_table("voucher_options")
    op.drop_index("ix_vouchers_tender_type", table_name="vouchers")
    op.drop_column("vouchers", "tender_type")
