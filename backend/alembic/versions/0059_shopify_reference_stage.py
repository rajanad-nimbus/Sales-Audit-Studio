"""Stage complete Shopify reference snapshots before applying them."""
import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shopify_reference_stage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sync_id", "category", "code", name="uq_shopify_reference_stage_sync_code"),
    )
    op.create_index(
        "ix_shopify_reference_stage_sync_id", "shopify_reference_stage", ["sync_id"]
    )
    op.create_index(
        "ix_shopify_reference_stage_sync_id_id", "shopify_reference_stage", ["sync_id", "id"]
    )
    op.create_index(
        "ix_shopify_reference_stage_created_at", "shopify_reference_stage", ["created_at"]
    )
    op.create_table(
        "shopify_reference_sync_state",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("applied_generation", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    sync_state = sa.table(
        "shopify_reference_sync_state",
        sa.column("key", sa.String(length=50)),
        sa.column("generation", sa.Integer()),
        sa.column("applied_generation", sa.Integer()),
    )
    op.bulk_insert(sync_state, [{"key": "Shopify", "generation": 0, "applied_generation": 0}])


def downgrade():
    op.drop_table("shopify_reference_sync_state")
    op.drop_index("ix_shopify_reference_stage_created_at", table_name="shopify_reference_stage")
    op.drop_index("ix_shopify_reference_stage_sync_id_id", table_name="shopify_reference_stage")
    op.drop_index("ix_shopify_reference_stage_sync_id", table_name="shopify_reference_stage")
    op.drop_table("shopify_reference_stage")
