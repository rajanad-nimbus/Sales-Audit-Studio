"""export batches record how many records are back-posts
Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa
revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("export_batches", sa.Column("backposted_count", sa.Integer, nullable=False, server_default="0"))
def downgrade():
    op.drop_column("export_batches", "backposted_count")
