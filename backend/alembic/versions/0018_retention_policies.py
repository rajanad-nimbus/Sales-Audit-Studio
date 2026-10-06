"""production retention policies

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa
revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None
def upgrade(): op.create_table("retention_policies", sa.Column("id", sa.String(36), primary_key=True), sa.Column("dataset", sa.String(64), nullable=False, unique=True), sa.Column("retain_days", sa.Integer(), nullable=False), sa.Column("archive_before_purge", sa.Boolean(), nullable=False), sa.Column("updated_by", sa.String(255)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
def downgrade(): op.drop_table("retention_policies")
