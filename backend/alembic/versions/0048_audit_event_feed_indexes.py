"""Add concurrent indexes for case and entity audit timelines."""

from alembic import op


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_audit_events_case_seq", ["case_id", "seq"]),
    ("ix_audit_events_object_seq", ["object_type", "object_id", "seq"]),
)


def upgrade():
    # These indexes target the append-only audit table, which can be large in production.
    # Concurrent creation keeps normal reads and writes available while PostgreSQL builds them.
    with op.get_context().autocommit_block():
        for name, columns in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.create_index(
                name,
                "audit_events",
                columns,
                postgresql_concurrently=True,
            )


def downgrade():
    with op.get_context().autocommit_block():
        for name, _columns in reversed(INDEXES):
            op.drop_index(name, table_name="audit_events", postgresql_concurrently=True)
