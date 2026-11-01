"""Scheduled sync (daily or weekly at a time of day) for sources that Nimbus pulls itself."""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None

COLUMNS = (
    ("sync_frequency", sa.String(8)),
    ("sync_time", sa.String(5)),
    ("sync_weekday", sa.Integer()),
    ("sync_timezone", sa.String(64)),
    ("last_scheduled_run_at", sa.DateTime(timezone=True)),
    ("last_scheduled_status", sa.String(16)),
    ("last_scheduled_message", sa.String(255)),
)


def upgrade():
    for name, kind in COLUMNS:
        op.add_column("source_settings", sa.Column(name, kind, nullable=True))


def downgrade():
    for name, _ in reversed(COLUMNS):
        op.drop_column("source_settings", name)
