"""Serialize audit sequence allocation with hash-chain insertion order."""

from alembic import op


revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

AUDIT_INSERT_TRIGGER = """
CREATE OR REPLACE FUNCTION audit_events_before_insert() RETURNS trigger AS $$
DECLARE prev text;
BEGIN
    -- Allocate sequence values only after obtaining the same lock that serializes chain writes.
    PERFORM pg_advisory_xact_lock(727001);
    NEW.seq := nextval('audit_events_seq');
    SELECT hash INTO prev FROM audit_events ORDER BY seq DESC LIMIT 1;
    NEW.prev_hash := prev;
    NEW.hash := audit_event_hash(prev, NEW);
    RETURN NEW;
END $$ LANGUAGE plpgsql
"""


def upgrade():
    op.execute(AUDIT_INSERT_TRIGGER)


def downgrade():
    # Keep the integrity fix when rolling back application code; the old trigger is unsafe.
    op.execute(AUDIT_INSERT_TRIGGER)
