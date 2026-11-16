"""Create governed transaction field audit rules."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "configured_transaction_audit_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("record_type", sa.String(length=50), nullable=True),
        sa.Column("conditions", json_type, nullable=False, server_default="[]"),
        sa.Column("match_mode", sa.String(length=24), nullable=False, server_default="Match"),
        sa.Column("start_date", sa.String(length=10), nullable=True),
        sa.Column("end_date", sa.String(length=10), nullable=True),
        sa.Column("location_traits", json_type, nullable=False, server_default="[]"),
        sa.Column("execution_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("stop_processing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="Worksheet"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("submitted_by", sa.String(length=255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_configured_transaction_audit_rule_name"),
    )
    op.create_index(
        "ix_configured_transaction_audit_rules_status_order",
        "configured_transaction_audit_rules",
        ["status", "enabled", "execution_order"],
    )
    op.add_column(
        "exceptions", sa.Column("detection_rule_name", sa.String(length=150), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("exceptions", "detection_rule_name")
    op.drop_index(
        "ix_configured_transaction_audit_rules_status_order",
        table_name="configured_transaction_audit_rules",
    )
    op.drop_table("configured_transaction_audit_rules")
