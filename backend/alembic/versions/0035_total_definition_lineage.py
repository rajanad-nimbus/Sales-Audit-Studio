"""Preserve the definition revision used for each configured total."""
import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("audit_totals") as batch:
        batch.add_column(sa.Column("definition_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("definition_revision", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_audit_totals_definition", "audit_total_definitions", ["definition_id"], ["id"])


def downgrade():
    with op.batch_alter_table("audit_totals") as batch:
        batch.drop_constraint("fk_audit_totals_definition", type_="foreignkey")
        batch.drop_column("definition_revision")
        batch.drop_column("definition_id")
