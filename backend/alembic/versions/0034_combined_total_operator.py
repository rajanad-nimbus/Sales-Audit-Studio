"""Record the arithmetic operation for combined totals."""
import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("audit_total_definitions", sa.Column("combined_operator", sa.String(1), nullable=True))


def downgrade():
    op.drop_column("audit_total_definitions", "combined_operator")
