"""Use the workbook's exact item lists on the remote/store sales and paid in/out totals."""

import json

from alembic import context, op

revision = "0129"
down_revision = "0128"
branch_labels = None
depends_on = None

RESTRICTIONS = {
    "5f90e619-de8a-5f13-910b-286323977714": [
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
    "e6f96805-9e31-542a-876e-5b4c2c089a88": [
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
    ],
    "5a09a9ad-849f-5993-8859-d48d21e2e11f": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM050OUT,NM050BSI"}
    ],
    "4d05a6f9-8b4b-5fbd-8e14-cd5885b09bdc": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM842OUT,NM842BSI"}
    ],
    "ba482f3f-3eee-5410-b115-18f2eb2847e4": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM857OUT,NM857BSI"}
    ],
    "9960fd9f-414e-54ac-81e1-7ca86ba16a42": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM971OUT,NM984OUT,NM971BSI,NM984BSI",
        }
    ],
    "0cb58e1c-afc3-55e0-bacc-4bd9cf784166": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM981OUT,NM981BSI"}
    ],
    "e525c649-1981-5617-96b1-58a7b6e23aa8": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM972BSI,NM991BSI,NM972OUT,NM991OUT",
        }
    ],
    "3ffb82a2-7ee8-5064-9db0-4b39ada5d454": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM895OUT,NM895BSI"}
    ],
    "2aebd5b0-58a5-52fa-b684-40fa8bb429f0": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM846OUT,NM846BSI"}
    ],
    "7274c6f9-398e-5bc1-b22d-3ed68342d23a": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM831OUT,NM831BSI"}
    ],
    "b61d6e10-174e-57b4-8dd1-caf3ba3527e3": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM840OUT,NM840BSI"}
    ],
    "0c700e1a-0f03-5b27-8d96-2932d932d2a7": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM841OUT,NM841BSI"}
    ],
    "abd0278b-e883-5d88-a290-4a73f2f659ee": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM832OUT,NM832BSI"}
    ],
    "a6f7bad6-8276-537d-b675-a66bfce465ab": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM858OUT,NM858BSI"}
    ],
    "c73e034e-373a-50c3-a33a-6defb4031e59": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM847OUT,NM847BSI"}
    ],
    "5906f952-a8de-5311-b296-ef0786523380": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM849OUT,NM849BSI"}
    ],
    "6812c4a5-0629-5244-9108-e8fe635b3e6f": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM835OUT,NM835BSI"}
    ],
    "3e68b96f-a5f7-56c8-9aea-7a202b36693a": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM051OUT,NM052OUT,NM051BSI,NM052BSI",
        }
    ],
    "3258b163-fc87-5b2c-8cf8-2d000c63ad0d": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM853OUT,NM853BSI"}
    ],
    "e7d1d8f1-b923-5651-a4d6-e2e11636f4f7": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM854OUT,NM854BSI"}
    ],
    "c694a369-b2bc-5f29-9707-dcaff3de976b": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM877OUT,NM887OUT,NM877BSI,NM887BSI",
        }
    ],
    "afc74f6e-5a35-5ea1-99a8-d757cdc75b3c": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM848OUT,NM848BSI"}
    ],
    "f4984d4c-eba4-57bc-af2e-780a99a30f8a": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM894OUT,NM977OUT,NM978OUT,NM894BSI,NM977BSI,NM978BSI",
        }
    ],
    "39b09c2d-bf0c-52eb-87c0-2448bcca0989": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM893OUT,NM893BSI"}
    ],
    "b1120079-3edb-5b7d-9b83-d3b50caacb35": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM845BSI,NM845OUT"}
    ],
    "ec1093a4-d37a-5fdf-9a3f-fc03dbced72c": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
        }
    ],
    "bb22c957-2767-59db-be19-cd68a14a710b": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
        }
    ],
    "393c5095-be3f-58fd-8902-46252f96dccb": [
        {
            "realm": "Line",
            "field": "item_code",
            "operator": "in",
            "value": "NM877BSI,NM877OUT,NM887BSI,NM887OUT,NM832BSI,NM832OUT",
        }
    ],
    "bc9a8efe-f0ae-5b24-83d3-5b09b7934d9b": [
        {"realm": "Line", "field": "item_code", "operator": "in", "value": "NM981BSI,NM981OUT"}
    ],
}


def upgrade():
    postgres = context.is_offline_mode() or op.get_bind().dialect.name == "postgresql"
    for identifier, restrictions in RESTRICTIONS.items():
        literal = json.dumps(restrictions).replace("'", "''")
        value = f"'{literal}'::json" if postgres else f"'{literal}'"
        op.execute(
            f"update audit_total_definitions set restrictions = {value} "
            f"where id = '{identifier}' and updated_by = 'system'"
        )


def downgrade():
    """Register lists cannot be expressed before this revision; the totals keep the lists."""
