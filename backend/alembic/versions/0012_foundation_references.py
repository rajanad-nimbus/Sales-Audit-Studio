"""retail foundation references

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("foundation_references", sa.Column("id", sa.String(36), primary_key=True), sa.Column("category", sa.String(50), nullable=False), sa.Column("code", sa.String(100), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("attributes", sa.JSON(), nullable=False), sa.Column("updated_by", sa.String(255)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("category", "code", name="uq_foundation_reference_category_code"))
    op.create_index("ix_foundation_references_category", "foundation_references", ["category"])

def downgrade():
    op.drop_table("foundation_references")
