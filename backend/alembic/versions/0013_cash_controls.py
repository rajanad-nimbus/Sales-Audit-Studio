"""cash office controls

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("cash_controls", sa.Column("id", sa.String(36), primary_key=True), sa.Column("store_id", sa.String(50), nullable=False), sa.Column("business_date", sa.String(10), nullable=False), sa.Column("register_id", sa.String(50)), sa.Column("cashier_id", sa.String(100)), sa.Column("tender_type", sa.String(50), nullable=False), sa.Column("declared_cash", sa.Numeric(15,2), nullable=False), sa.Column("paid_out", sa.Numeric(15,2), nullable=False), sa.Column("safe_drop", sa.Numeric(15,2), nullable=False), sa.Column("expected_amount", sa.Numeric(15,2)), sa.Column("variance_amount", sa.Numeric(15,2)), sa.Column("status", sa.String(24), nullable=False), sa.Column("declared_by", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_cash_controls_store_id", "cash_controls", ["store_id"]); op.create_index("ix_cash_controls_business_date", "cash_controls", ["business_date"]); op.create_index("ix_cash_controls_register_id", "cash_controls", ["register_id"]); op.create_index("ix_cash_controls_cashier_id", "cash_controls", ["cashier_id"])

def downgrade():
    op.drop_table("cash_controls")
