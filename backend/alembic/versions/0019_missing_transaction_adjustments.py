"""allow governed missing transaction proposals

Revision ID: 0019
Revises: 0018
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column("transaction_adjustments", "transaction_id", existing_type=__import__("sqlalchemy").String(36), nullable=True)

def downgrade():
    op.alter_column("transaction_adjustments", "transaction_id", existing_type=__import__("sqlalchemy").String(36), nullable=False)
