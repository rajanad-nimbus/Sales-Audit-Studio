"""Limit the BSI and Outlet sales totals to the workbook's store area ranges (2000-2999 and 3000-3999)."""

import json

from alembic import context, op

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None

RESTRICTIONS = {
    "0053c174-3cfd-5768-a22b-59d5d313307b": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "in",
            "value": "880,881,889,893,892,895,896,897",
        },
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "not_in",
            "value": "NM050BSI,NM842BSI,NM857BSI,NM971BSI,NM984BSI,NM981BSI,NM972BSI,NM991BSI,NM895BSI,NM846BSI,NM840BSI,NM831BSI,NM841BSI,NM832BSI,NM858BSI,NM847BSI,NM849BSI,NM835BSI,NM051BSI,NM052BSI,NM853BSI,NM854BSI,NM877BSI,NM887BSI,NM848BSI,NM894BSI,NM977BSI,NM978BSI,NM893BSI,NM845BSI",
        },
        {"realm": "Transaction", "field": "store_area", "operator": "gte", "value": "2000"},
        {"realm": "Transaction", "field": "store_area", "operator": "lte", "value": "2999"},
    ],
    "12d26da6-db6a-539e-a190-c9ef9f0ea341": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "in",
            "value": "880,881,889,893,892,895,896,897",
        },
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "not_in",
            "value": "NM050OUT,NM842OUT,NM857OUT,NM971OUT,NM984OUT,NM981OUT,NM972OUT,NM991OUT,NM895OUT,NM846OUT,NM840OUT,NM831OUT,NM841OUT,NM832OUT,NM858OUT,NM847OUT,NM849OUT,NM835OUT,NM051OUT,NM052OUT,NM853OUT,NM854OUT,NM877OUT,NM887OUT,NM848OUT,NM894OUT,NM977OUT,NM978OUT,NM893OUT,NM845OUT",
        },
        {"realm": "Transaction", "field": "store_area", "operator": "gte", "value": "3000"},
        {"realm": "Transaction", "field": "store_area", "operator": "lte", "value": "3999"},
    ],
    "0b35321c-e83b-5105-8784-b3bc9a9ca765": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "not_in",
            "value": "880,881,889,893,892,895,896,897",
        },
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "not_in",
            "value": "NM050BSI,NM842BSI,NM857BSI,NM971BSI,NM984BSI,NM981BSI,NM972BSI,NM991BSI,NM895BSI,NM846BSI,NM840BSI,NM831BSI,NM841BSI,NM832BSI,NM858BSI,NM847BSI,NM849BSI,NM835BSI,NM051BSI,NM052BSI,NM853BSI,NM854BSI,NM877BSI,NM887BSI,NM848BSI,NM894BSI,NM977BSI,NM978BSI,NM893BSI,NM845BSI",
        },
        {"realm": "Transaction", "field": "store_area", "operator": "gte", "value": "2000"},
        {"realm": "Transaction", "field": "store_area", "operator": "lte", "value": "2999"},
    ],
    "ef30fc0c-f541-548c-b250-629ff383725e": [
        {
            "realm": "Transaction",
            "field": "register_id",
            "operator": "not_in",
            "value": "880,881,889,893,892,895,896,897",
        },
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "not_in",
            "value": "NM050OUT,NM842OUT,NM857OUT,NM971OUT,NM984OUT,NM981OUT,NM972OUT,NM991OUT,NM895OUT,NM846OUT,NM840OUT,NM831OUT,NM841OUT,NM832OUT,NM858OUT,NM847OUT,NM849OUT,NM835OUT,NM051OUT,NM052OUT,NM853OUT,NM854OUT,NM877OUT,NM887OUT,NM848OUT,NM894OUT,NM977OUT,NM978OUT,NM893OUT,NM845OUT",
        },
        {"realm": "Transaction", "field": "store_area", "operator": "gte", "value": "3000"},
        {"realm": "Transaction", "field": "store_area", "operator": "lte", "value": "3999"},
    ],
}


def apply(restrictions_by_id):
    postgres = context.is_offline_mode() or op.get_bind().dialect.name == "postgresql"
    for identifier, restrictions in restrictions_by_id.items():
        literal = json.dumps(restrictions).replace("'", "''")
        value = f"'{literal}'::json" if postgres else f"'{literal}'"
        op.execute(
            f"update audit_total_definitions set restrictions = {value} "
            f"where id = '{identifier}' and updated_by = 'system'"
        )


def upgrade():
    apply(RESTRICTIONS)


def downgrade():
    apply(
        {
            identifier: [r for r in restrictions if r["field"] != "store_area"]
            for identifier, restrictions in RESTRICTIONS.items()
        }
    )
