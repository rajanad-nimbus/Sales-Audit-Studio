"""Let a total sum the tax recorded in transaction detail, and install the store tax total."""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from apps.totals.standard import TAX_TOTALS

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

JSON_COLUMNS = ("transaction_types", "realms", "joins", "parameters", "rollups", "restrictions", "combined_total_ids",
                "location_traits", "usages")


def upgrade():
    op.add_column("audit_total_definitions", sa.Column("measure", sa.String(16), nullable=False, server_default="Amount"))
    table = sa.table("audit_total_definitions", *[sa.column(c, sa.JSON) for c in JSON_COLUMNS], *[sa.column(c) for c in (
        "id", "name", "revision", "description", "category", "calculation_basis", "status", "aggregation", "measure",
        "source_system", "level", "required_by_system", "display_group", "active", "updated_by", "corresponds_to_rtlog",
        "system_calculated", "created_with_wizard")], sa.column("updated_at", sa.DateTime(timezone=True)))
    existing = {r[0] for r in op.get_bind().execute(sa.text("select name from audit_total_definitions"))}
    rows = [{"id": str(uuid.uuid4()), "revision": 1, "calculation_basis": "Raw", "status": "Active", "level": "Store",
             "required_by_system": False, "active": True, "updated_by": "system", "corresponds_to_rtlog": False,
             "system_calculated": True, "created_with_wizard": True, "updated_at": datetime.now(timezone.utc),
             **{c: [] for c in JSON_COLUMNS if c != "transaction_types"}, **t}
            for t in TAX_TOTALS if t["name"] not in existing]
    if rows:
        op.bulk_insert(table, rows)


def downgrade():
    names = ", ".join(f"'{t['name']}'" for t in TAX_TOTALS)
    op.execute(f"delete from audit_total_definitions where updated_by = 'system' and name in ({names})")
    op.drop_column("audit_total_definitions", "measure")
