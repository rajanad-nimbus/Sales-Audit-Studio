"""Add the unknown-tender audit error to the error catalog."""

import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

COLUMNS = ("code", "description", "short_description", "form", "tab", "recommended_solution")
ROW = (
    "TENDER_NOT_FOUND",
    "A transaction was paid with a tender type that is not in the Tender reference data.",
    "Unknown tender type",
    "Transaction Detail",
    None,
    "Add the tender type to Foundation Data, or correct the transaction's tender.",
)


def upgrade():
    values = ", ".join("NULL" if v is None else "'" + v.replace("'", "''") + "'" for v in ROW)
    op.execute(
        sa.text(
            f"INSERT INTO sales_audit_error_definitions ({', '.join(COLUMNS)}) "
            f"SELECT {values} WHERE NOT EXISTS "
            f"(SELECT 1 FROM sales_audit_error_definitions WHERE code = 'TENDER_NOT_FOUND')"
        )
    )


def downgrade():
    op.execute(sa.text("DELETE FROM sales_audit_error_definitions WHERE code = 'TENDER_NOT_FOUND'"))
