"""Index source records by source and event time for store-day controls."""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_source_records_system_event_time", "source_records", ["source_system", "source_event_time"])


def downgrade():
    op.drop_index("ix_source_records_system_event_time", table_name="source_records")
