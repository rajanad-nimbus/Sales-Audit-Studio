"""Add composite indexes for cash-office store and business-date queries."""

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_cash_controls_store_date_tender",
            "cash_controls",
            ["store_id", "business_date", "tender_type"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_cash_deposits_store_date",
            "cash_deposits",
            ["store_id", "business_date"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_cash_deposits_store_date",
            table_name="cash_deposits",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_cash_controls_store_date_tender",
            table_name="cash_controls",
            postgresql_concurrently=True,
        )
