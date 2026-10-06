"""cash-office deposit reconciliation

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("cash_deposits", sa.Column("id", sa.String(36), primary_key=True), sa.Column("store_id", sa.String(50), nullable=False), sa.Column("business_date", sa.String(10), nullable=False), sa.Column("deposit_reference", sa.String(100), nullable=False, unique=True), sa.Column("bank_account", sa.String(100)), sa.Column("deposited_amount", sa.Numeric(15,2), nullable=False), sa.Column("expected_amount", sa.Numeric(15,2)), sa.Column("variance_amount", sa.Numeric(15,2)), sa.Column("status", sa.String(24), nullable=False), sa.Column("recorded_by", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_cash_deposits_store_id", "cash_deposits", ["store_id"])
    op.create_index("ix_cash_deposits_business_date", "cash_deposits", ["business_date"])

def downgrade():
    op.drop_table("cash_deposits")
