"""Carry the client's tender code on each tender and point the per-tender totals at it."""

import sqlalchemy as sa
from alembic import context, op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def retarget(old: str, new: str) -> None:
    """Rewrite the field name in the system totals' stored restrictions (text replace keeps it portable)."""
    postgres = context.is_offline_mode() or op.get_bind().dialect.name == "postgresql"
    text = "restrictions::text" if postgres else "restrictions"
    replaced = f'replace({text}, \'"field": "{old}"\', \'"field": "{new}"\')'
    op.execute(
        "update audit_total_definitions set restrictions = "
        + (f"{replaced}::json" if postgres else replaced)
        + " where updated_by = 'system' and restrictions like '%\"realm\": \"Tender\"%'".replace(
            "restrictions like", f"{text} like"
        )
    )


def upgrade():
    op.add_column(
        "transaction_tenders", sa.Column("client_tender_code", sa.String(20), nullable=True)
    )
    retarget("tender_type", "client_tender_code")


def downgrade():
    retarget("client_tender_code", "tender_type")
    op.drop_column("transaction_tenders", "client_tender_code")
