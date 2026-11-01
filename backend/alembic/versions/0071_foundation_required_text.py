"""Require nonblank foundation reference identifiers and labels."""

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "ck_foundation_references_required_text"
REQUIRED_TEXT_CHECK = (
    "length(trim(category)) > 0 AND length(trim(code)) > 0 AND length(trim(name)) > 0"
)


def upgrade():
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM foundation_references "
            "WHERE length(trim(category)) = 0 OR length(trim(code)) = 0 "
            "OR length(trim(name)) = 0) THEN "
            "RAISE EXCEPTION 'foundation_references has blank category, code, or name; repair those rows before upgrading'; "
            "END IF; END $$;"
        )
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "foundation_references",
        REQUIRED_TEXT_CHECK,
    )


def downgrade():
    op.drop_constraint(
        CONSTRAINT_NAME,
        "foundation_references",
        type_="check",
    )
