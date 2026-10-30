"""Install the standard Sales Audit totals, including the system-required OVRSHT_S."""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from apps.totals.standard import STANDARD_TOTALS

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

JSON_COLUMNS = {"transaction_types", "realms", "joins", "parameters", "rollups", "restrictions", "combined_total_ids",
                "location_traits", "usages"}


def upgrade():
    table = sa.table("audit_total_definitions", *[sa.column(c, sa.DateTime(timezone=True)) if c == "updated_at" else sa.column(c, sa.JSON) if c in JSON_COLUMNS else sa.column(c) for c in (
        "id", "name", "revision", "description", "category", "over_short_group", "over_short_operator", "calculation_basis",
        "status", "aggregation", "source_system", "transaction_types", "tender_type", "level", "required_by_system",
        "display_group", "active", "updated_by", "updated_at", "corresponds_to_rtlog", "system_calculated", "created_with_wizard",
        "realms", "joins", "parameters", "rollups", "restrictions", "combined_total_ids", "location_traits", "usages")])
    existing = {r[0] for r in op.get_bind().execute(sa.text("select name from audit_total_definitions"))}
    rows = [{"id": str(uuid.uuid4()), "revision": 1, "calculation_basis": "Raw", "status": "Active", "level": "Store",
             "required_by_system": False, "active": True, "updated_by": "system", "over_short_group": None,
             "over_short_operator": None, "tender_type": None, "updated_at": datetime.now(timezone.utc), "corresponds_to_rtlog": False,
             "system_calculated": True, "created_with_wizard": True,
             **{c: [] for c in JSON_COLUMNS if c != "transaction_types"}, **t}
            for t in STANDARD_TOTALS if t["name"] not in existing]
    if rows:
        op.bulk_insert(table, rows)


def downgrade():
    names = ", ".join(f"'{t['name']}'" for t in STANDARD_TOTALS)
    op.execute(f"delete from audit_total_definitions where updated_by = 'system' and name in ({names})")
