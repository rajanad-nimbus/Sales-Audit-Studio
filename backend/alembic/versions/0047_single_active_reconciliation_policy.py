"""Ensure at most one reconciliation policy is active."""

from alembic import op
import sqlalchemy as sa


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None
INDEX_NAME = "uq_reconciliation_policies_one_active"


def upgrade():
    # Preserve the most recently updated active policy and deactivate older rows.
    op.execute(sa.text(
        "UPDATE reconciliation_policies SET active = false "
        "WHERE active = true AND id NOT IN ("
        "SELECT id FROM reconciliation_policies WHERE active = true "
        "ORDER BY updated_at DESC, id DESC LIMIT 1)"
    ))
    op.create_index(
        INDEX_NAME,
        "reconciliation_policies",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active IS TRUE"),
        sqlite_where=sa.text("active = 1"),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="reconciliation_policies")
