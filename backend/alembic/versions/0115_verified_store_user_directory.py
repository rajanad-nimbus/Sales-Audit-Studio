"""Cache verified store-scoped identities for safe assignment administration."""

import sqlalchemy as sa
from alembic import op

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "verified_store_users",
        sa.Column("assignment_identifier", sa.String(255), primary_key=True),
        sa.Column("user_identifier", sa.String(255), nullable=False),
        sa.Column("identity_id", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_verified_store_users_identity_id", "verified_store_users", ["identity_id"])
    op.create_index(
        "ix_verified_store_users_user_identifier", "verified_store_users", ["user_identifier"]
    )
    op.create_index("ix_verified_store_users_role", "verified_store_users", ["role"])
    op.create_index(
        "ix_verified_store_users_last_seen_at", "verified_store_users", ["last_seen_at"]
    )


def downgrade():
    op.drop_index("ix_verified_store_users_last_seen_at", table_name="verified_store_users")
    op.drop_index("ix_verified_store_users_role", table_name="verified_store_users")
    op.drop_index("ix_verified_store_users_user_identifier", table_name="verified_store_users")
    op.drop_index("ix_verified_store_users_identity_id", table_name="verified_store_users")
    op.drop_table("verified_store_users")
