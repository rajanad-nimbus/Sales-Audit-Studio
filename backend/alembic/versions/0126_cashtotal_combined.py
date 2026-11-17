"""CASHTOTAL is Cash plus Check, not a tender of its own (the workbook's definition)."""

from alembic import op

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None

CASHTOTAL = "0cacb3b8-eae7-5502-a9bb-25f51cdf4fbc"


def upgrade():
    op.execute(
        "update audit_total_definitions set calculation_basis = 'Combined', realms = '[]', joins = '[]', "
        "parameters = '[]', restrictions = '[]', "
        'combined_total_ids = \'["0ea19e0a-6c40-5334-bf7f-868f66adfb6e", "6e828b00-25c1-5ea4-9fd9-c7362f96757e"]\', combined_total_operators = \'["+"]\' '
        f"where id = '{CASHTOTAL}' and updated_by = 'system'"
    )


def downgrade():
    op.execute(
        "update audit_total_definitions set calculation_basis = 'Raw', realms = '[\"Transaction\", \"Tender\"]', "
        'joins = \'[{"parent_realm": "Transaction", "child_realm": "Tender"}]\', '
        'parameters = \'[{"realm": "Tender", "field": "amount"}]\', '
        'restrictions = \'[{"realm": "Tender", "field": "tender_type", "operator": "eq", "value": "CASHTOTAL"}]\', '
        "combined_total_ids = '[]', combined_total_operators = '[]' "
        f"where id = '{CASHTOTAL}' and updated_by = 'system'"
    )
