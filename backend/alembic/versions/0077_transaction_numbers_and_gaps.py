"""Store the register's transaction number and add the missing-transaction audit error."""

import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

COLUMNS = ("code", "description", "short_description", "form", "tab", "recommended_solution")
ROW = (
    "MISSING_TRAN",
    "One or more transaction numbers are missing from a register's sequence for the business day.",
    "Missing transaction number",
    "Transaction Detail",
    None,
    "Re-poll the register, or confirm the missing numbers were voided or training transactions.",
)


def upgrade():
    with op.batch_alter_table("canonical_transactions") as batch:
        batch.add_column(sa.Column("transaction_number", sa.String(30)))
    values = ", ".join("NULL" if v is None else "'" + v.replace("'", "''") + "'" for v in ROW)
    op.execute(
        sa.text(
            f"INSERT INTO sales_audit_error_definitions ({', '.join(COLUMNS)}) "
            f"SELECT {values} WHERE NOT EXISTS "
            f"(SELECT 1 FROM sales_audit_error_definitions WHERE code = 'MISSING_TRAN')"
        )
    )


def downgrade():
    op.execute(sa.text("DELETE FROM sales_audit_error_definitions WHERE code = 'MISSING_TRAN'"))
    with op.batch_alter_table("canonical_transactions") as batch:
        batch.drop_column("transaction_number")
