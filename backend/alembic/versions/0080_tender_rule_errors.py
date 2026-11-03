"""Add the tender reference and tender limit audit errors to the error catalog."""

import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

COLUMNS = ("code", "description", "short_description", "form", "tab", "recommended_solution")
ROWS = [
    (
        "TENDER_REF_REQ",
        "A tender that requires a reference was recorded without one.",
        "Tender reference required",
        "Transaction Detail",
        None,
        "Add the reference or card slip number from the receipt.",
    ),
    (
        "TENDER_OVER_LIMIT",
        "A tender amount is above the limit set for that tender type.",
        "Tender over limit",
        "Transaction Detail",
        None,
        "Confirm the payment with the store manager, or correct the amount.",
    ),
]


def upgrade():
    for row in ROWS:
        values = ", ".join("NULL" if v is None else "'" + v.replace("'", "''") + "'" for v in row)
        op.execute(
            sa.text(
                f"INSERT INTO sales_audit_error_definitions ({', '.join(COLUMNS)}) "
                f"SELECT {values} WHERE NOT EXISTS "
                f"(SELECT 1 FROM sales_audit_error_definitions WHERE code = '{row[0]}')"
            )
        )


def downgrade():
    op.execute(
        sa.text(
            "DELETE FROM sales_audit_error_definitions "
            "WHERE code IN ('TENDER_REF_REQ', 'TENDER_OVER_LIMIT')"
        )
    )
