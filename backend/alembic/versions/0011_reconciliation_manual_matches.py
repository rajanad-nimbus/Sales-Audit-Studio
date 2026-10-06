"""governed manual reconciliation matches

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("reconciliation_matches", sa.Column("id", sa.String(36), primary_key=True), sa.Column("transaction_ids", sa.JSON(), nullable=False), sa.Column("match_type", sa.String(50), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("rationale", sa.Text(), nullable=False), sa.Column("proposed_by", sa.String(255), nullable=False), sa.Column("reviewed_by", sa.String(255)), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_reconciliation_matches_status", "reconciliation_matches", ["status"])

def downgrade():
    op.drop_table("reconciliation_matches")
