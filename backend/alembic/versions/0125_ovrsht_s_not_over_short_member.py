"""Stop the system OVRSHT_S total double counting against the workbook's Over/Short totals.

The client totals (0124) define the Accountable For and Accounted For sides explicitly, so the generic
store-level OVRSHT_S stays as a required, referenceable total but no longer feeds the Over/Short calculation.
"""

from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "update audit_total_definitions set category = 'Miscellaneous', over_short_group = null, "
        "over_short_operator = null where name = 'OVRSHT_S' and updated_by = 'system'"
    )


def downgrade():
    op.execute(
        "update audit_total_definitions set category = 'Over/Short', over_short_group = 'Accountable For', "
        "over_short_operator = '+' where name = 'OVRSHT_S' and updated_by = 'system'"
    )
