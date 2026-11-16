"""Version voucher jurisdiction policy and retain final routing decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vouchers", sa.Column("escheat_locations", sa.JSON(), nullable=True))
    op.add_column("vouchers", sa.Column("escheat_routing_snapshot", sa.JSON(), nullable=True))
    op.create_table(
        "voucher_routing_policy",
        sa.Column("id", sa.String(20), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hq_location", sa.JSON(), nullable=True),
        sa.Column("income_partner", sa.String(100), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("revision", sa.String(36), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 'default'", name="ck_voucher_routing_singleton"),
    )


def downgrade():
    op.drop_table("voucher_routing_policy")
    op.drop_column("vouchers", "escheat_routing_snapshot")
    op.drop_column("vouchers", "escheat_locations")
