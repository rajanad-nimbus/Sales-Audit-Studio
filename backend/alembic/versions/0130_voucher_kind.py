"""Classify voucher lines by number range and use it on the gift card, MCR and virtual gift card totals."""

import json

import sqlalchemy as sa
from alembic import context, op

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None

RESTRICTIONS = {
    "2717421c-44ff-5d1a-a4a2-a0fa6e3efbf7": [
        {"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "GIFTCARD,MCR"}
    ],
    "9e1d6933-67f0-582e-a183-b71daab2978e": [
        {"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "VIRTUAL"}
    ],
    "ce544790-16d4-5de8-9999-d0ee4eea3e5e": [
        {"realm": "Line", "field": "voucher_kind", "operator": "in", "value": "GIFTCARD,MCR"}
    ],
}


OLD = {
    "2717421c-44ff-5d1a-a4a2-a0fa6e3efbf7": "GC",  # GFTCRDSL
    "ce544790-16d4-5de8-9999-d0ee4eea3e5e": "GC",  # MCRCASHED
    "9e1d6933-67f0-582e-a183-b71daab2978e": "VGC",  # VIRTGCSL
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
    op.add_column("transaction_lines", sa.Column("voucher_kind", sa.String(12), nullable=True))
    apply(RESTRICTIONS)


def downgrade():
    apply(
        {
            identifier: [
                {"realm": "Line", "field": "item_code", "operator": "contains", "value": code}
            ]
            for identifier, code in OLD.items()
        }
    )
    op.drop_column("transaction_lines", "voucher_kind")
