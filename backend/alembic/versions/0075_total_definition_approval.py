"""Record who submitted and who approved a total-definition revision.

A draft now goes Worksheet -> Pending Approval -> Active, and the approver must be a
different user from the editor and the submitter.
"""

import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("audit_total_definitions") as batch:
        batch.add_column(sa.Column("submitted_by", sa.String(255)))
        batch.add_column(sa.Column("submitted_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("submitted_impact_fingerprint", sa.String(64)))
        batch.add_column(sa.Column("approved_by", sa.String(255)))
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True)))


def downgrade():
    # Submissions awaiting approval return to Worksheet so they stay out of calculation.
    op.execute(
        "UPDATE audit_total_definitions SET status = 'Worksheet' WHERE status = 'Pending Approval'"
    )
    with op.batch_alter_table("audit_total_definitions") as batch:
        for column in (
            "approved_at",
            "approved_by",
            "submitted_impact_fingerprint",
            "submitted_at",
            "submitted_by",
        ):
            batch.drop_column(column)
