"""Index the paginated case-evidence feed."""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_evidence_snapshots_case_retrieved_id"


def upgrade():
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "evidence_snapshots",
            ["case_id", "retrieved_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX_NAME,
            table_name="evidence_snapshots",
            postgresql_concurrently=True,
        )
