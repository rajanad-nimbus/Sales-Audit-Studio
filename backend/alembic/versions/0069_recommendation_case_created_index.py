"""Index recommendations for latest-per-case lookups."""

import sqlalchemy as sa
from alembic import op


revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_recommendations_case_created_id"


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "recommendations",
            ["case_id", "created_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX_NAME,
            table_name="recommendations",
            postgresql_concurrently=True,
        )
