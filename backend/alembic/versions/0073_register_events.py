"""Store register events (voids, no-sales, paid-in/out, pulls, loans, opens, closes) and their audit errors."""

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

COLUMNS = ("code", "description", "short_description", "form", "tab", "recommended_solution")
ROWS = [
    (
        "VOID_NO_ORIG",
        "A post-void names an original transaction that is not on file for that store.",
        "Void without original transaction",
        "Transaction Detail",
        None,
        "Find the original transaction number and register, or reject the void.",
    ),
    (
        "PAIDOUT_REASON_REQ",
        "A paid-in or paid-out has no reason code.",
        "Missing paid-in/out reason",
        "Transaction Detail",
        None,
        "Enter the reason the cash moved.",
    ),
    (
        "NOSALE_EXCESS",
        "A register opened its drawer without a sale more often than the allowed number of times in a day.",
        "Excessive no-sales",
        "Non Transactional",
        None,
        "Review the register journal with the cashier and the store manager.",
    ),
]


def upgrade():
    op.create_table(
        "register_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_record_id", sa.String(255), nullable=False, unique=True),
        sa.Column("source_format", sa.String(30), nullable=False),
        sa.Column("store_id", sa.String(50), nullable=False),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("register_id", sa.String(50)),
        sa.Column("cashier_id", sa.String(100)),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("sub_type", sa.String(30)),
        sa.Column("transaction_number", sa.String(30), nullable=False),
        sa.Column("original_transaction_number", sa.String(30)),
        sa.Column("original_register_id", sa.String(50)),
        sa.Column("reason_code", sa.String(30)),
        sa.Column("amount", sa.Numeric(15, 2)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("audited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("audit_code", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_register_events_store_id", "register_events", ["store_id"])
    op.create_index("ix_register_events_business_date", "register_events", ["business_date"])
    op.create_index("ix_register_events_event_type", "register_events", ["event_type"])
    op.create_index(
        "ix_register_events_store_date_type",
        "register_events",
        ["store_id", "business_date", "event_type"],
    )
    op.create_index("ix_register_events_unaudited", "register_events", ["audited", "business_date"])
    # Plain INSERT ... WHERE NOT EXISTS so the migration also renders offline (--sql).
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
            "WHERE code IN ('VOID_NO_ORIG', 'PAIDOUT_REASON_REQ', 'NOSALE_EXCESS')"
        )
    )
    op.drop_table("register_events")
