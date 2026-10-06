"""export items become a per-record manifest with state
Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa
revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None
def upgrade():
    # Rows that exist today were all recorded after delivery, so they are Delivered.
    op.add_column("export_items", sa.Column("state", sa.String(20), nullable=False, server_default="Delivered"))
    op.add_column("export_items", sa.Column("record_ref", sa.String(120), nullable=True))
    op.add_column("export_items", sa.Column("store_id", sa.String(50), nullable=True))
    op.add_column("export_items", sa.Column("business_date", sa.String(10), nullable=True))
    op.add_column("export_items", sa.Column("backpost", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("export_items", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
def downgrade():
    for c in ("delivered_at", "backpost", "business_date", "store_id", "record_ref", "state"):
        op.drop_column("export_items", c)
