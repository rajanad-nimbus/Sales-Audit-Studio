"""Install the standard Sales Audit totals, including the system-required OVRSHT_S."""

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

JSON_COLUMNS = {
    "transaction_types",
    "realms",
    "joins",
    "parameters",
    "rollups",
    "restrictions",
    "combined_total_ids",
    "location_traits",
    "usages",
}
INTEGER_COLUMNS = {"revision", "display_group"}
BOOLEAN_COLUMNS = {
    "required_by_system",
    "active",
    "corresponds_to_rtlog",
    "system_calculated",
    "created_with_wizard",
}

# Keep migration seed data here instead of importing mutable application code.
STANDARD_TOTALS = [
    {
        "name": "OVRSHT_S",
        "description": "Store-level over/short",
        "category": "Over/Short",
        "over_short_group": "Accountable For",
        "over_short_operator": "+",
        "aggregation": "Sum",
        "source_system": "POS",
        "transaction_types": ["Sale", "Return"],
        "required_by_system": True,
        "display_group": 1,
    },
    {
        "name": "SALES_POS",
        "description": "Store sales",
        "category": "Sales",
        "aggregation": "Sum",
        "source_system": "POS",
        "transaction_types": ["Sale"],
        "display_group": 10,
    },
    {
        "name": "RETURNS_POS",
        "description": "Store returns",
        "category": "Returns",
        "aggregation": "Sum",
        "source_system": "POS",
        "transaction_types": ["Return"],
        "display_group": 11,
    },
    {
        "name": "SALES_ECOM",
        "description": "E-commerce sales",
        "category": "Sales",
        "aggregation": "Sum",
        "source_system": "Shopify",
        "transaction_types": ["Order"],
        "display_group": 12,
    },
    {
        "name": "RETURNS_ECOM",
        "description": "E-commerce returns",
        "category": "Returns",
        "aggregation": "Sum",
        "source_system": "Shopify",
        "transaction_types": ["Refund"],
        "display_group": 13,
    },
    *[
        {
            "name": f"TEND_{tender.upper()}",
            "description": f"{tender} tender",
            "category": "Tender",
            "aggregation": "Sum",
            "source_system": "POS",
            "transaction_types": ["Sale", "Return"],
            "tender_type": tender,
            "display_group": 20 + index,
        }
        for index, tender in enumerate(("Cash", "Card", "Voucher", "GiftCard", "StoreCredit"))
    ],
]


def upgrade():
    columns = (
        "id",
        "name",
        "revision",
        "description",
        "category",
        "over_short_group",
        "over_short_operator",
        "calculation_basis",
        "status",
        "aggregation",
        "source_system",
        "transaction_types",
        "tender_type",
        "level",
        "required_by_system",
        "display_group",
        "active",
        "updated_by",
        "updated_at",
        "corresponds_to_rtlog",
        "system_calculated",
        "created_with_wizard",
        "realms",
        "joins",
        "parameters",
        "rollups",
        "restrictions",
        "combined_total_ids",
        "location_traits",
        "usages",
    )
    table = sa.table(
        "audit_total_definitions",
        *[
            sa.column(name, sa.DateTime(timezone=True))
            if name == "updated_at"
            else sa.column(name, sa.JSON)
            if name in JSON_COLUMNS
            else sa.column(name, sa.Integer())
            if name in INTEGER_COLUMNS
            else sa.column(name, sa.Boolean())
            if name in BOOLEAN_COLUMNS
            else sa.column(name, sa.Text())
            if name == "description"
            else sa.column(name, sa.String())
            for name in columns
        ],
    )
    existing = (
        set()
        if context.is_offline_mode()
        else {
            r[0] for r in op.get_bind().execute(sa.text("select name from audit_total_definitions"))
        }
    )
    rows = [
        {
            "id": str(uuid.uuid4()),
            "revision": 1,
            "calculation_basis": "Raw",
            "status": "Active",
            "level": "Store",
            "required_by_system": False,
            "active": True,
            "updated_by": "system",
            "over_short_group": None,
            "over_short_operator": None,
            "tender_type": None,
            "updated_at": datetime.now(timezone.utc),
            "corresponds_to_rtlog": False,
            "system_calculated": True,
            "created_with_wizard": True,
            **{c: [] for c in JSON_COLUMNS if c != "transaction_types"},
            **t,
        }
        for t in STANDARD_TOTALS
        if t["name"] not in existing
    ]
    if rows:
        if context.is_offline_mode():
            offline_rows = [
                {
                    **row,
                    **{
                        name: sa.cast(sa.literal(json.dumps(row[name]), type_=sa.String()), sa.JSON)
                        for name in JSON_COLUMNS
                    },
                }
                for row in rows
            ]
            op.execute(
                pg_insert(table)
                .values(offline_rows)
                .on_conflict_do_nothing(index_elements=["name"])
            )
        else:
            op.bulk_insert(table, rows)


def downgrade():
    names = ", ".join(f"'{t['name']}'" for t in STANDARD_TOTALS)
    op.execute(
        f"delete from audit_total_definitions where updated_by = 'system' and name in ({names})"
    )
