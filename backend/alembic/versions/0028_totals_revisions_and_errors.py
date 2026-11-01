"""Expand total definitions and add the common Sales Audit error catalog."""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

def upgrade():
    try:
        op.drop_constraint("audit_total_definitions_name_key", "audit_total_definitions", type_="unique")
    except Exception:
        pass
    cols = [
        ("revision", sa.Integer(), {"nullable": False, "server_default": "1"}),
        ("description", sa.Text(), {}), ("start_date", sa.String(10), {}), ("end_date", sa.String(10), {}),
        ("category", sa.String(50), {"nullable": False, "server_default": "Miscellaneous"}),
        ("over_short_group", sa.String(32), {}), ("over_short_operator", sa.String(8), {}),
        ("calculation_basis", sa.String(32), {"nullable": False, "server_default": "Raw"}),
        ("status", sa.String(24), {"nullable": False, "server_default": "Worksheet"}),
        ("corresponds_to_rtlog", sa.Boolean(), {"nullable": False, "server_default": "0"}),
        ("system_calculated", sa.Boolean(), {"nullable": False, "server_default": "1"}),
        ("created_with_wizard", sa.Boolean(), {"nullable": False, "server_default": "1"}),
        ("required_by_system", sa.Boolean(), {"nullable": False, "server_default": "0"}),
        ("display_group", sa.Integer(), {"nullable": False, "server_default": "0"}),
        ("realms", sa.JSON(), {"nullable": False, "server_default": "[]"}), ("joins", sa.JSON(), {"nullable": False, "server_default": "[]"}),
        ("parameters", sa.JSON(), {"nullable": False, "server_default": "[]"}), ("rollups", sa.JSON(), {"nullable": False, "server_default": "[]"}),
        ("restrictions", sa.JSON(), {"nullable": False, "server_default": "[]"}), ("combined_total_ids", sa.JSON(), {"nullable": False, "server_default": "[]"}),
        ("location_traits", sa.JSON(), {"nullable": False, "server_default": "[]"}), ("usages", sa.JSON(), {"nullable": False, "server_default": "[]"}),
        ("sql_statement", sa.Text(), {}),
    ]
    for name, typ, kwargs in cols:
        op.add_column("audit_total_definitions", sa.Column(name, typ, **kwargs))
    op.create_index("ix_audit_total_definitions_name_revision", "audit_total_definitions", ["name", "revision"], unique=True)
    op.create_table("sales_audit_error_definitions",
        sa.Column("code", sa.String(64), primary_key=True), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("short_description", sa.String(255), nullable=False), sa.Column("form", sa.String(100)),
        sa.Column("tab", sa.String(100)), sa.Column("recommended_solution", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"))
    rows = [
        ("DISC_REASON_REQ", "Discount reason cannot be blank for a Store discount.", "Missing discount reason", "Transaction Detail", "Tender", "Enter a Discount reason."),
        ("DUP_REC", "Duplicate Record. The transaction has been sent to the reject file.", "Duplicate transaction", None, None, "Input file is corrupt. Either edit the file or re-poll the store."),
        ("DUP_TRAN", "Duplicate Transaction Number. The transaction has been sent to the reject file.", "Duplicate transaction", None, None, "Input file is corrupt. Either edit the file or re-poll the store."),
        ("INVLD_SADT", "Invalid Discount Type.", "Invalid discount type", "Transaction Detail", "Item", "Choose a Discount Type from the list."),
        ("MISSING_TRAN_BIG_GAP", "A large number of transactions are missing.", "Large transaction gap", "Non Transactional", None, "The input file may be corrupt. Try re-polling the store."),
        ("SKU_NOT_FOUND", "The item is either empty or does not exist in RMS.", "Item not found", "Transaction Detail", "Item", "Choose a item from the list."),
        ("TRAN_OUT_BAL", "Transaction is out of balance. The total of the items and payment amount does not equal the total of the tenders.", "Transaction out of balance", "Transaction Detail", None, "Adjust the Items or Tender or Payment Amount as required."),
        ("TTEND_TTI_STIN", "Tender Type ID is not part of the Tender Type Group or it is empty.", "Invalid tender type", "Transaction Detail", "Tender", "Choose a Tender Type from the list."),
        ("UPC_NOT_FOUND", "This UPC does not exist in the RMS.", "UPC not found", "Transaction Detail", "Item", "Choose a UPC from the list."),
    ]
    op.bulk_insert(sa.table("sales_audit_error_definitions", *[sa.Column(x, sa.String()) for x in ("code", "description", "short_description", "form", "tab", "recommended_solution")]), [dict(zip(("code", "description", "short_description", "form", "tab", "recommended_solution"), r)) for r in rows])

def downgrade():
    op.drop_table("sales_audit_error_definitions")
    op.drop_index("ix_audit_total_definitions_name_revision", table_name="audit_total_definitions")
