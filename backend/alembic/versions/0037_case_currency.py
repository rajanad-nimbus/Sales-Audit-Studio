"""Preserve the currency of reconciliation findings."""
from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cases", sa.Column("currency", sa.String(3), nullable=True))


def downgrade():
    op.drop_column("cases", "currency")
