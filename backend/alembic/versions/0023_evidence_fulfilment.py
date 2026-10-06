"""evidence requests can be fulfilled by a person
Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa
revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("evidence_requests", sa.Column("response", sa.Text, nullable=True))
    op.add_column("evidence_requests", sa.Column("reference", sa.String(255), nullable=True))
    op.add_column("evidence_requests", sa.Column("fulfilled_by", sa.String(255), nullable=True))
    op.add_column("evidence_requests", sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True))
def downgrade():
    for c in ("fulfilled_at", "fulfilled_by", "reference", "response"):
        op.drop_column("evidence_requests", c)
