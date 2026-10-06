"""sales transaction detail: lines, tax, discounts, tenders, returns
Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa
revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("canonical_transactions", sa.Column("original_reference", sa.String(255), nullable=True))
    op.add_column("canonical_transactions", sa.Column("original_transaction_id", sa.String(36), sa.ForeignKey("canonical_transactions.id"), nullable=True))
    op.add_column("canonical_transactions", sa.Column("return_reason", sa.String(255), nullable=True))
    op.add_column("canonical_transactions", sa.Column("detail_status", sa.String(20), nullable=False, server_default="No detail"))
    op.create_index("ix_ct_original_reference", "canonical_transactions", ["original_reference"])
    op.create_index("ix_ct_original_transaction_id", "canonical_transactions", ["original_transaction_id"])
    op.create_index("ix_ct_detail_status", "canonical_transactions", ["detail_status"])
    tx = lambda: sa.Column("transaction_id", sa.String(36), sa.ForeignKey("canonical_transactions.id"), nullable=False, index=True)
    op.create_table("transaction_lines", sa.Column("id", sa.String(36), primary_key=True), tx(),
                    sa.Column("line_no", sa.Integer, nullable=False), sa.Column("item_code", sa.String(100), nullable=False, index=True),
                    sa.Column("description", sa.String(255)), sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
                    sa.Column("unit_price", sa.Numeric(15, 2), nullable=False), sa.Column("gross_amount", sa.Numeric(15, 2), nullable=False),
                    sa.Column("tax_code", sa.String(30)), sa.Column("tax_rate", sa.Numeric(7, 4), nullable=False, server_default="0"),
                    sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"), sa.Column("return_reason", sa.String(255)))
    op.create_table("transaction_taxes", sa.Column("id", sa.String(36), primary_key=True), tx(),
                    sa.Column("tax_code", sa.String(30), nullable=False), sa.Column("tax_name", sa.String(100), nullable=False),
                    sa.Column("rate", sa.Numeric(7, 4), nullable=False, server_default="0"),
                    sa.Column("taxable_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
                    sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"))
    op.create_table("transaction_discounts", sa.Column("id", sa.String(36), primary_key=True), tx(),
                    sa.Column("line_no", sa.Integer), sa.Column("kind", sa.String(20), nullable=False, index=True),
                    sa.Column("code", sa.String(100), index=True), sa.Column("description", sa.String(255)),
                    sa.Column("amount", sa.Numeric(15, 2), nullable=False))
    op.create_table("transaction_tenders", sa.Column("id", sa.String(36), primary_key=True), tx(),
                    sa.Column("tender_type", sa.String(30), nullable=False, index=True), sa.Column("amount", sa.Numeric(15, 2), nullable=False),
                    sa.Column("reference", sa.String(255)), sa.Column("authorization", sa.String(100)))
def downgrade():
    for t in ("transaction_tenders", "transaction_discounts", "transaction_taxes", "transaction_lines"):
        op.drop_table(t)
    for i in ("ix_ct_detail_status", "ix_ct_original_transaction_id", "ix_ct_original_reference"):
        op.drop_index(i, table_name="canonical_transactions")
    for c in ("detail_status", "return_reason", "original_transaction_id", "original_reference"):
        op.drop_column("canonical_transactions", c)
