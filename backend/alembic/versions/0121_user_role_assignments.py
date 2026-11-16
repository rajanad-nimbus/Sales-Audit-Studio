"""Business roles assigned locally to verified Ontology Studio identities."""

import sqlalchemy as sa
from alembic import op

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_role_assignments",
        sa.Column("assignment_identifier", sa.String(255), primary_key=True),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("assigned_by", sa.String(255), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade():
    op.drop_table("user_role_assignments")
