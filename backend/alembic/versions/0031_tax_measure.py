"""Let a total sum the tax recorded in transaction detail, and install the store tax total."""
import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

JSON_COLUMNS = ("transaction_types", "realms", "joins", "parameters", "rollups", "restrictions", "combined_total_ids",
                "location_traits", "usages")
INTEGER_COLUMNS = {"revision", "display_group"}
BOOLEAN_COLUMNS = {"required_by_system", "active", "corresponds_to_rtlog", "system_calculated", "created_with_wizard"}

# Freeze this revision's seed rows so future application edits cannot alter migration behavior.
TAX_TOTALS = [
    {"name": "TAX_POS", "description": "Tax on store transactions", "category": "Tax", "aggregation": "Sum",
     "measure": "Tax", "source_system": "POS", "transaction_types": ["Sale", "Return"], "display_group": 30},
    {"name": "TAX_ECOM", "description": "Tax on e-commerce orders and refunds", "category": "Tax",
     "aggregation": "Sum", "measure": "Tax", "source_system": "Shopify",
     "transaction_types": ["Order", "Refund"], "display_group": 31},
]


def upgrade():
    op.add_column("audit_total_definitions", sa.Column("measure", sa.String(16), nullable=False, server_default="Amount"))
    columns = (
        "id", "name", "revision", "description", "category", "calculation_basis", "status", "aggregation", "measure",
        "source_system", "level", "required_by_system", "display_group", "active", "updated_by", "corresponds_to_rtlog",
        "system_calculated", "created_with_wizard", *JSON_COLUMNS, "updated_at"
    )
    table = sa.table("audit_total_definitions", *[
        sa.column(name, sa.DateTime(timezone=True)) if name == "updated_at"
        else sa.column(name, sa.JSON) if name in JSON_COLUMNS
        else sa.column(name, sa.Integer()) if name in INTEGER_COLUMNS
        else sa.column(name, sa.Boolean()) if name in BOOLEAN_COLUMNS
        else sa.column(name, sa.Text()) if name == "description"
        else sa.column(name, sa.String())
        for name in columns
    ])
    existing = set() if context.is_offline_mode() else {
        r[0] for r in op.get_bind().execute(sa.text("select name from audit_total_definitions"))
    }
    rows = [{"id": str(uuid.uuid4()), "revision": 1, "calculation_basis": "Raw", "status": "Active", "level": "Store",
             "required_by_system": False, "active": True, "updated_by": "system", "corresponds_to_rtlog": False,
             "system_calculated": True, "created_with_wizard": True, "updated_at": datetime.now(timezone.utc),
             **{c: [] for c in JSON_COLUMNS if c != "transaction_types"}, **t}
            for t in TAX_TOTALS if t["name"] not in existing]
    if rows:
        if context.is_offline_mode():
            offline_rows = [{**row, **{
                name: sa.cast(sa.literal(json.dumps(row[name]), type_=sa.String()), sa.JSON)
                for name in JSON_COLUMNS
            }} for row in rows]
            op.execute(pg_insert(table).values(offline_rows).on_conflict_do_nothing(index_elements=["name"]))
        else:
            op.bulk_insert(table, rows)


def downgrade():
    names = ", ".join(f"'{t['name']}'" for t in TAX_TOTALS)
    op.execute(f"delete from audit_total_definitions where updated_by = 'system' and name in ({names})")
    op.drop_column("audit_total_definitions", "measure")
