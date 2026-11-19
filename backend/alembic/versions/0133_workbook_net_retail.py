"""Apply the workbook's net retail measure and shared voucher tender IDs."""

import json

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import JSON

revision = "0133"
down_revision = "0132"
branch_labels = None
depends_on = None

# Frozen system definition IDs from the workbook's NET_RETAIL columns.
TOTAL_IDS = (
    "2717421c-44ff-5d1a-a4a2-a0fa6e3efbf7",
    "5f90e619-de8a-5f13-910b-286323977714",
    "e6f96805-9e31-542a-876e-5b4c2c089a88",
    "0053c174-3cfd-5768-a22b-59d5d313307b",
    "12d26da6-db6a-539e-a190-c9ef9f0ea341",
    "0b35321c-e83b-5105-8784-b3bc9a9ca765",
    "ef30fc0c-f541-548c-b250-629ff383725e",
    "9e1d6933-67f0-582e-a183-b71daab2978e",
    "ce544790-16d4-5de8-9999-d0ee4eea3e5e",
    "56340f8b-7dc8-5892-bb80-462ec6d8e87b",
    "5a09a9ad-849f-5993-8859-d48d21e2e11f",
    "4d05a6f9-8b4b-5fbd-8e14-cd5885b09bdc",
    "ba482f3f-3eee-5410-b115-18f2eb2847e4",
    "9960fd9f-414e-54ac-81e1-7ca86ba16a42",
    "0cb58e1c-afc3-55e0-bacc-4bd9cf784166",
    "e525c649-1981-5617-96b1-58a7b6e23aa8",
    "3ffb82a2-7ee8-5064-9db0-4b39ada5d454",
    "2aebd5b0-58a5-52fa-b684-40fa8bb429f0",
    "7274c6f9-398e-5bc1-b22d-3ed68342d23a",
    "b61d6e10-174e-57b4-8dd1-caf3ba3527e3",
    "0c700e1a-0f03-5b27-8d96-2932d932d2a7",
    "abd0278b-e883-5d88-a290-4a73f2f659ee",
    "a6f7bad6-8276-537d-b675-a66bfce465ab",
    "c73e034e-373a-50c3-a33a-6defb4031e59",
    "5906f952-a8de-5311-b296-ef0786523380",
    "6812c4a5-0629-5244-9108-e8fe635b3e6f",
    "3e68b96f-a5f7-56c8-9aea-7a202b36693a",
    "3258b163-fc87-5b2c-8cf8-2d000c63ad0d",
    "e7d1d8f1-b923-5651-a4d6-e2e11636f4f7",
    "c694a369-b2bc-5f29-9707-dcaff3de976b",
    "afc74f6e-5a35-5ea1-99a8-d757cdc75b3c",
    "f4984d4c-eba4-57bc-af2e-780a99a30f8a",
    "39b09c2d-bf0c-52eb-87c0-2448bcca0989",
    "b1120079-3edb-5b7d-9b83-d3b50caacb35",
    "ec1093a4-d37a-5fdf-9a3f-fc03dbced72c",
    "bb22c957-2767-59db-be19-cd68a14a710b",
    "393c5095-be3f-58fd-8902-46252f96dccb",
    "bc9a8efe-f0ae-5b24-83d3-5b09b7934d9b",
)


def apply(field):
    definitions = sa.table(
        "audit_total_definitions",
        sa.column("id", sa.String),
        sa.column("updated_by", sa.String),
        sa.column("parameters", sa.JSON),
        sa.column("restrictions", sa.JSON),
    )
    parameter = [{"realm": "Line", "field": field}]
    voucher_usage = {"realm": "Tender", "field": "client_tender_code"}
    if field == "net_retail":
        voucher_usage.update(operator="in", value="GIFTCRDU,MCRISSUE")
    else:
        voucher_usage.update(operator="eq", value="GIFTCRDU")
    for identifiers, column, value in (
        (TOTAL_IDS, "parameters", parameter),
        (("8505c0dd-5cdb-5c6a-b03a-4a64d7970d35",), "restrictions", [voucher_usage]),
    ):
        if context.is_offline_mode():
            value = sa.cast(sa.literal(json.dumps(value)), JSON)
        op.execute(
            definitions.update()
            .where(definitions.c.id.in_(identifiers), definitions.c.updated_by == "system")
            .values({column: value})
        )


def upgrade():
    apply("net_retail")


def downgrade():
    apply("gross_amount")
