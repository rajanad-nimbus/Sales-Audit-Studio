"""Per-tender purge windows and permanent voucher identities."""

import sqlalchemy as sa
from alembic import op

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_vouchers_tender_age", "vouchers", [sa.text("lower(tender_type)"), "issued_on", "id"]
    )
    with op.batch_alter_table("voucher_options") as batch:
        batch.add_column(sa.Column("purge_days", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_voucher_options_purge_days",
            "purge_days IS NULL OR purge_days BETWEEN 0 AND 36500",
        )
    op.create_table(
        "voucher_identities",
        sa.Column("voucher_number", sa.String(100), primary_key=True),
        sa.Column("voucher_id", sa.String(36), nullable=False, unique=True),
        sa.Column("issued_store_id", sa.String(50), nullable=False),
        sa.Column("tender_type", sa.String(100), nullable=False),
        sa.Column("issued_on", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_status", sa.String(16), nullable=True),
        sa.Column("finalized_on", sa.String(10), nullable=True),
        sa.Column(
            "retention_run_id",
            sa.String(36),
            sa.ForeignKey("retention_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.CheckConstraint("amount > 0", name="ck_voucher_identity_amount_positive"),
        sa.CheckConstraint(
            "(retired_at IS NULL AND final_status IS NULL AND finalized_on IS NULL AND retention_run_id IS NULL) OR "
            "(retired_at IS NOT NULL AND final_status IS NOT NULL AND final_status IN ('Redeemed', 'Escheated') "
            "AND finalized_on IS NOT NULL AND retention_run_id IS NOT NULL)",
            name="ck_voucher_identity_retirement",
        ),
    )
    op.create_index(
        "ix_voucher_identities_issued_store_id", "voucher_identities", ["issued_store_id"]
    )
    op.create_index("ix_voucher_identities_retired_at", "voucher_identities", ["retired_at"])
    op.execute(
        sa.text(
            "INSERT INTO voucher_identities (voucher_number, voucher_id, issued_store_id, tender_type, issued_on, amount, currency, reserved_at) "
            "SELECT voucher_number, id, issued_store_id, tender_type, issued_on, amount, currency, created_at FROM vouchers"
        )
    )


def downgrade():
    op.drop_index("ix_vouchers_tender_age", table_name="vouchers")
    op.drop_table("voucher_identities")
    with op.batch_alter_table("voucher_options") as batch:
        batch.drop_constraint("ck_voucher_options_purge_days", type_="check")
        batch.drop_column("purge_days")
