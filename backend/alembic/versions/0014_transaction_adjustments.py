"""governed transaction adjustments

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("transaction_adjustments", sa.Column("id", sa.String(36), primary_key=True), sa.Column("transaction_id", sa.String(36), sa.ForeignKey("canonical_transactions.id"), nullable=False), sa.Column("adjustment_type", sa.String(50), nullable=False), sa.Column("proposed_values", sa.JSON(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("proposed_by", sa.String(255), nullable=False), sa.Column("reviewed_by", sa.String(255)), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_transaction_adjustments_transaction_id", "transaction_adjustments", ["transaction_id"]); op.create_index("ix_transaction_adjustments_status", "transaction_adjustments", ["status"])

def downgrade():
    op.drop_table("transaction_adjustments")
