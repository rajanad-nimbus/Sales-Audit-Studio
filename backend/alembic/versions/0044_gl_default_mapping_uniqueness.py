"""Allow only one fallback GL account mapping per transaction type."""

import sqlalchemy as sa
from alembic import context, op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_gl_cross_reference_default_by_type"


def upgrade():
    if not context.is_offline_mode():
        conflicts = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT transaction_type FROM gl_cross_references WHERE tender_type IS NULL "
                    "GROUP BY transaction_type HAVING COUNT(*) > 1 ORDER BY transaction_type"
                )
            )
            .scalars()
            .all()
        )
        if conflicts:
            transaction_types = ", ".join(conflicts)
            raise RuntimeError(
                "Cannot enforce one default GL mapping per transaction type; resolve duplicate "
                f"mappings for: {transaction_types}"
            )
    op.create_index(
        INDEX_NAME,
        "gl_cross_references",
        ["transaction_type"],
        unique=True,
        postgresql_where=sa.text("tender_type IS NULL"),
        sqlite_where=sa.text("tender_type IS NULL"),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="gl_cross_references")
