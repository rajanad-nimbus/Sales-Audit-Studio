"""Preserve the cashier associated with a source transaction.

Revision ID: 0033
Revises: 0032
"""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("canonical_transactions", sa.Column("cashier_id", sa.String(100), nullable=True))


def downgrade():
    op.drop_column("canonical_transactions", "cashier_id")
