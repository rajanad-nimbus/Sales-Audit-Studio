"""Make Worksheet total-definition revisions drafts that calculation ignores.

Every existing revision was live regardless of its status, so existing Worksheet
rows become Active to keep today's audit results unchanged.
"""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE audit_total_definitions SET status = 'Active' WHERE status = 'Worksheet'")
    with op.batch_alter_table("audit_total_definitions") as batch:
        batch.alter_column("status", server_default="Active")


def downgrade():
    # Drafts created after the upgrade cannot be told apart from migrated rows,
    # so the downgrade only restores the column default.
    with op.batch_alter_table("audit_total_definitions") as batch:
        batch.alter_column("status", server_default="Worksheet")
