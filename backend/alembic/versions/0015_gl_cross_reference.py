"""gl cross reference

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("gl_cross_references", sa.Column("id", sa.String(36), primary_key=True), sa.Column("transaction_type", sa.String(50), nullable=False), sa.Column("tender_type", sa.String(50)), sa.Column("debit_account", sa.String(100), nullable=False), sa.Column("credit_account", sa.String(100), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("updated_by", sa.String(255)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("transaction_type", "tender_type", name="uq_gl_cross_reference"))
def downgrade(): op.drop_table("gl_cross_references")
