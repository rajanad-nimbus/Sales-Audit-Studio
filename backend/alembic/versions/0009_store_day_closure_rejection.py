"""store day closure rejection rationale

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("store_day_closure_requests", sa.Column("review_reason", sa.Text()))

def downgrade():
    op.drop_column("store_day_closure_requests", "review_reason")
