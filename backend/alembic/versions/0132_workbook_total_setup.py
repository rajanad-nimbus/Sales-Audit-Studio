"""Apply omitted filters and leading subtraction from the ReSA workbook."""

import json

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import JSON as PGJSON

revision = "0132"
down_revision = "0131"
branch_labels = None
depends_on = None

# Frozen before and after values allow a downgrade without importing the current catalog.
FILTERS = {
    "0ea19e0a-6c40-5334-bf7f-868f66adfb6e": (
        [{"realm": "Tender", "field": "client_tender_code", "operator": "eq", "value": "CASH"}],
        [
            {
                "realm": "Tender",
                "field": "client_tender_code",
                "operator": "in",
                "value": "CASH,MALLCERT",
            }
        ],
    ),
    "2717421c-44ff-5d1a-a4a2-a0fa6e3efbf7": (
        [{"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "GIFTCARD,MCR"}],
        [
            {"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "GIFTCARD,MCR"},
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "eq", "value": "GCN"},
        ],
    ),
    "5f90e619-de8a-5f13-910b-286323977714": (
        [
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "in",
                "value": "880,881,889,893,892,895,896,897",
            },
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM050OUT,NM842OUT,NM857OUT,NM971OUT,NM984OUT,NM981OUT,NM972OUT,NM991OUT,NM895OUT,NM846OUT,NM840OUT,NM831OUT,NM841OUT,NM832OUT,NM858OUT,NM847OUT,NM849OUT,NM835OUT,NM051OUT,NM052OUT,NM853OUT,NM854OUT,NM877OUT,NM887OUT,NM848OUT,NM894OUT,NM977OUT,NM978OUT,NM893OUT,NM845OUT",
            },
        ],
        [
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "in",
                "value": "880,881,889,893,892,895,896,897",
            },
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM050OUT,NM842OUT,NM857OUT,NM971OUT,NM984OUT,NM981OUT,NM972OUT,NM991OUT,NM895OUT,NM846OUT,NM840OUT,NM831OUT,NM841OUT,NM832OUT,NM858OUT,NM847OUT,NM849OUT,NM835OUT,NM051OUT,NM052OUT,NM853OUT,NM854OUT,NM877OUT,NM887OUT,NM848OUT,NM894OUT,NM977OUT,NM978OUT,NM893OUT,NM845OUT",
            },
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
        ],
    ),
    "e6f96805-9e31-542a-876e-5b4c2c089a88": (
        [
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "in",
                "value": "880,881,889,893,892,895,896,897",
            },
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM050BSI,NM842BSI,NM857BSI,NM971BSI,NM984BSI,NM981BSI,NM972BSI,NM991BSI,NM895BSI,NM846BSI,NM840BSI,NM831BSI,NM841BSI,NM832BSI,NM858BSI,NM847BSI,NM849BSI,NM835BSI,NM051BSI,NM052BSI,NM853BSI,NM854BSI,NM877BSI,NM887BSI,NM848BSI,NM894BSI,NM977BSI,NM978BSI,NM893BSI,NM845BSI",
            },
        ],
        [
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "in",
                "value": "880,881,889,893,892,895,896,897",
            },
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM050BSI,NM842BSI,NM857BSI,NM971BSI,NM984BSI,NM981BSI,NM972BSI,NM991BSI,NM895BSI,NM846BSI,NM840BSI,NM831BSI,NM841BSI,NM832BSI,NM858BSI,NM847BSI,NM849BSI,NM835BSI,NM051BSI,NM052BSI,NM853BSI,NM854BSI,NM877BSI,NM887BSI,NM848BSI,NM894BSI,NM977BSI,NM978BSI,NM893BSI,NM845BSI",
            },
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
        ],
    ),
    "0053c174-3cfd-5768-a22b-59d5d313307b": (
        [
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
        [
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
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "in", "value": "ITEM,REF"},
        ],
    ),
    "12d26da6-db6a-539e-a190-c9ef9f0ea341": (
        [
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
        [
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
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "in", "value": "ITEM,REF"},
        ],
    ),
    "0b35321c-e83b-5105-8784-b3bc9a9ca765": (
        [
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
        [
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
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "in", "value": "ITEM,REF"},
        ],
    ),
    "ef30fc0c-f541-548c-b250-629ff383725e": (
        [
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
        [
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
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "in", "value": "ITEM,REF"},
        ],
    ),
    "9e1d6933-67f0-582e-a183-b71daab2978e": (
        [{"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "VIRTUAL"}],
        [
            {"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "VIRTUAL"},
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "eq", "value": "GCN"},
        ],
    ),
    "ce544790-16d4-5de8-9999-d0ee4eea3e5e": (
        [{"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "GIFTCARD,MCR"}],
        [
            {
                "realm": "Line",
                "field": "item_voucher_kind",
                "operator": "in",
                "value": "GIFTCARD,MCR",
            },
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "eq", "value": "GCN"},
        ],
    ),
    "56340f8b-7dc8-5892-bb80-462ec6d8e87b": (
        [
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "in",
                "value": "880,881,889,893,892,895,896,897",
            }
        ],
        [
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "in",
                "value": "880,881,889,893,892,895,896,897",
            },
            {"realm": "Line", "field": "item_status", "operator": "in", "value": "Sale,Return"},
            {"realm": "Line", "field": "item_type", "operator": "in", "value": "ITEM,REF"},
        ],
    ),
    "0cb58e1c-afc3-55e0-bacc-4bd9cf784166": (
        [{"realm": "Line", "field": "item_code", "operator": "in", "value": "NM981OUT,NM981BSI"}],
        [
            {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM981OUT,NM981BSI"},
            {"realm": "Transaction", "field": "store_area", "operator": "gte", "value": "2000"},
            {"realm": "Transaction", "field": "store_area", "operator": "lte", "value": "2999"},
        ],
    ),
    "abd0278b-e883-5d88-a290-4a73f2f659ee": (
        [{"realm": "Line", "field": "item_code", "operator": "in", "value": "NM832OUT,NM832BSI"}],
        [
            {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM832OUT,NM832BSI"},
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "not_in",
                "value": "880,881,892,897,895,896",
            },
        ],
    ),
    "c694a369-b2bc-5f29-9707-dcaff3de976b": (
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877OUT,NM887OUT,NM877BSI,NM887BSI",
            }
        ],
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877OUT,NM887OUT,NM877BSI,NM887BSI",
            },
            {
                "realm": "Transaction",
                "field": "register_id",
                "operator": "not_in",
                "value": "880,881,892,897,895,896",
            },
        ],
    ),
    "ec1093a4-d37a-5fdf-9a3f-fc03dbced72c": (
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
            }
        ],
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
            },
            {"realm": "Transaction", "field": "register_id", "operator": "in", "value": "892,897"},
        ],
    ),
    "bb22c957-2767-59db-be19-cd68a14a710b": (
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
            }
        ],
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
            },
            {"realm": "Transaction", "field": "register_id", "operator": "in", "value": "895,896"},
        ],
    ),
    "393c5095-be3f-58fd-8902-46252f96dccb": (
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
            }
        ],
        [
            {
                "realm": "Line",
                "field": "item_code",
                "operator": "in",
                "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
            },
            {"realm": "Transaction", "field": "register_id", "operator": "in", "value": "880,881"},
        ],
    ),
    "bc9a8efe-f0ae-5b24-83d3-5b09b7934d9b": (
        [{"realm": "Line", "field": "item_code", "operator": "in", "value": "NM981BSI,NM981OUT"}],
        [
            {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM981BSI,NM981OUT"},
            {"realm": "Transaction", "field": "store_area", "operator": "gte", "value": "3000"},
            {"realm": "Transaction", "field": "store_area", "operator": "lte", "value": "3999"},
        ],
    ),
}


LEADING_SUBTRACTIONS = {
    "4b174bb6-effa-5d97-9a23-70cf50e64e04": ["-", "-", "-", "-", "-", "-", "-", "-", "-"],
    "51edb7ea-64b2-5cea-a065-16e038ae61c1": [
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
    ],
    "7b3f3b62-39bd-5b94-89be-de8a241a8bb2": [
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
    ],
    "860ad41b-c2f8-5b2c-81d8-effea018e0aa": ["-", "-", "-", "-", "-"],
    "cf599ede-7e1e-550c-b7c9-946995a33097": [
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
        "-",
    ],
}


def apply(version):
    definitions = sa.table(
        "audit_total_definitions",
        sa.column("id", sa.String),
        sa.column("updated_by", sa.String),
        sa.column("status", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("restrictions", sa.JSON),
        sa.column("combined_total_operators", sa.JSON),
    )
    for column, updates in (
        (
            "restrictions",
            {identifier: versions[version] for identifier, versions in FILTERS.items()},
        ),
        (
            "combined_total_operators",
            {
                identifier: ["-", *operators] if version else operators
                for identifier, operators in LEADING_SUBTRACTIONS.items()
            },
        ),
    ):
        for identifier, value in updates.items():
            if context.is_offline_mode():
                value = sa.cast(sa.literal(json.dumps(value)), PGJSON)
            op.execute(
                definitions.update()
                .where(definitions.c.id == identifier, definitions.c.updated_by == "system")
                .values({column: value})
            )

    # The workbook retires standalone mall certificates once the store handles them as cash.
    op.execute(
        definitions.update()
        .where(
            definitions.c.id.in_(
                (
                    "d6209ef5-b02c-508d-9e23-649c94078e3c",
                    "aed19979-39d0-5b4e-883b-a33521d56510",
                )
            ),
            definitions.c.updated_by == "system",
        )
        .values(status="Inactive" if version else "Active", active=not version)
    )


def upgrade():
    apply(1)


def downgrade():
    apply(0)
