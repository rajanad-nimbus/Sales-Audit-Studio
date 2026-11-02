"""Add the return-over-sold and tax-mismatch Sales Audit error definitions."""

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

COLUMNS = ("code", "description", "short_description", "form", "tab", "recommended_solution")
ROWS = [
    (
        "RETURN_OVER_SOLD",
        "The quantity returned for an item is greater than the quantity on the original sale.",
        "Return exceeds original sale",
        "Transaction Detail",
        "Item",
        "Compare the return with the original sale and correct or reject it.",
    ),
    (
        "TAX_MISMATCH",
        "The tax on the transaction does not match the tax rates of its items.",
        "Tax does not match rate",
        "Transaction Detail",
        "Item",
        "Correct the tax code or the tax amount.",
    ),
]


def upgrade():
    # Plain INSERT ... WHERE NOT EXISTS so the migration also renders offline (--sql).
    for row in ROWS:
        values = ", ".join("'" + value.replace("'", "''") + "'" for value in row)
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
            "WHERE code IN ('RETURN_OVER_SOLD', 'TAX_MISMATCH')"
        )
    )
